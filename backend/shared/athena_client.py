"""共享 Athena 查询层。

看板 API（backend/api）与 NL 问答 Agent（backend/agent）都复用本模块，保证：
- 查询口径单一来源（派生字段见 derivations.py，已在 ETL 落到 fact_user_daily）；
- 只暴露**参数化**查询构造器，绝不拼接任意 SQL / 用户输入不进 SQL 结构；
- `identity_filter` 兜底钩子（v1=None 全量；v2 由后端从 token 强制注入 email 过滤，满足 N3）。

执行模型：start_query_execution -> 轮询 -> get_query_results，带简单内存 TTL 缓存。
本模块可脱离 AWS 做单测（QueryLayer 依赖注入一个可替换的 runner）。
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

try:
    from backend.shared import derivations as _dv
except ImportError:  # Lambda 打包：shared 为顶层包（API）或与本模块同级（ETL）
    try:
        from shared import derivations as _dv  # type: ignore
    except ImportError:
        import derivations as _dv  # type: ignore


# 成本口径（方案 A）的 SQL 片段：每用户成本 = 月费（按档位）+ overage 成本。
# 月费由档位映射（derivations.TIER_MONTHLY_USD 单一来源）；overage = sum(est_cost)
# （est_cost 在日表已改为当天 overage 成本，可按天累加）。
def _tier_monthly_sql(tier_expr: str) -> str:
    """生成按档位映射月费的 CASE 表达式。tier_expr 是档位列/表达式（已在 SQL 中）。"""
    whens = " ".join(
        f"WHEN {_q(t)} THEN {p:g}"
        for t, p in _dv.TIER_MONTHLY_USD.items()
    )
    return f"(CASE {tier_expr} {whens} ELSE 0 END)"


# ---------------------------------------------------------------------------
# 安全：标识符与字面量校验（防注入）
# ---------------------------------------------------------------------------
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SORTABLE_COLUMNS = {
    "credits_used", "balance", "usage_rate", "usage_limit", "total_messages",
    "chat_conversations", "active_days", "est_cost", "email",
}
_SORT_DIRS = {"asc", "desc"}

# query_usage 的 metric / group_by 白名单：metric 会作为别名进入 SELECT/ORDER BY，
# 必须在构造 SQL 前校验，防止注入。
_ALLOWED_METRICS = frozenset({"credits", "messages", "est_cost", "active_days", "users"})
_ALLOWED_GROUP_BY = frozenset({"none", "department", "subscription_tier", "model", "date", "user"})


def _q(value: str) -> str:
    """把字符串安全地作为 SQL 单引号字面量（转义单引号）。"""
    return "'" + str(value).replace("'", "''") + "'"


def _valid_date(d: str) -> str:
    if not _DATE_RE.match(d or ""):
        raise ValueError(f"invalid date (expected YYYY-MM-DD): {d!r}")
    return d


def _valid_period(p: str) -> str:
    if not _PERIOD_RE.match(p or ""):
        raise ValueError(f"invalid period (expected YYYY-MM): {p!r}")
    return p


# ---------------------------------------------------------------------------
# 查询构造器（参数化；返回可直接执行的 SQL 字符串）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class QuerySpec:
    """一次查询：SQL + 说明（供 Agent 引用口径）。"""
    sql: str
    note: str = ""


class Queries:
    """所有看板/Agent 查询的唯一构造点。

    约定：
    - 时间范围用 `date` 字符串列（'YYYY-MM-DD'）做 BETWEEN，命中分区裁剪由 Athena 处理。
    - `identity_filter`：None=全量；否则形如 {"email": "a@b.com"}，强制注入 WHERE。
    """

    def __init__(self, db: str = "kiro_usage",
                 fact_daily: str = "fact_user_daily",
                 fact_model: str = "fact_user_model_daily"):
        self.db = db
        self.t_daily = f"{db}.{fact_daily}"
        self.t_model = f"{db}.{fact_model}"

    # -- 内部：构造 WHERE 片段 -------------------------------------------------
    def _where(self, date_from: Optional[str], date_to: Optional[str],
               identity_filter: Optional[dict], extra: Optional[list[str]] = None,
               alias: str = "") -> str:
        col = (alias + "." if alias else "")
        conds: list[str] = []
        if date_from:
            conds.append(f"{col}date >= {_q(_valid_date(date_from))}")
        if date_to:
            conds.append(f"{col}date <= {_q(_valid_date(date_to))}")
        if identity_filter:
            email = identity_filter.get("email")
            if email:
                conds.append(f"lower({col}email) = {_q(str(email).strip().lower())}")
        for e in (extra or []):
            conds.append(e)
        return ("WHERE " + " AND ".join(conds)) if conds else ""

    # -- KPI 汇总 + 档位分布 ---------------------------------------------------
    def summary(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        w = self._where(date_from, date_to, identity_filter)
        # KPI 计算分两路：
        #  per_user：用户级聚合（跨天 credits 求和、档位/额度取该用户最大、活跃=区间内有 credits）；
        #            超额用户=区间累计 credits 超过套餐额度（sum(credits)>usage_limit，对齐 Kiro 月度额度口径）。
        #  per_day ：按天 distinct 活跃用户，再取区间内 日活均值/峰值（对齐参照看板算法）。
        sql = f"""
        WITH per_user AS (
          SELECT email,
                 max(display_name) AS display_name,
                 max_by(subscription_tier, date) AS subscription_tier,
                 max(department) AS department,
                 sum(credits_used) AS credits_used,
                 sum(est_cost) AS est_cost,
                 max_by(usage_limit, date) AS usage_limit,
                 count(distinct date) AS active_days,
                 max(new_user) AS ever_new
          FROM {self.t_daily}
          {w}
          GROUP BY email
        ),
        per_day AS (
          SELECT date, count(distinct email) AS dau
          FROM {self.t_daily}
          {w}{' AND' if w else 'WHERE'} credits_used > 0
          GROUP BY date
        )
        SELECT
          (SELECT count(*) FROM per_user) AS total_users,
          (SELECT count_if(credits_used > 0) FROM per_user) AS active_users,
          (SELECT round(coalesce(sum(credits_used),0),1) FROM per_user) AS total_credits,
          (SELECT round(coalesce(sum(est_cost + {_tier_monthly_sql('subscription_tier')}),0),2) FROM per_user) AS est_cost,
          (SELECT count_if(usage_limit > 0 AND credits_used > usage_limit) FROM per_user) AS overage_users,
          (SELECT round(coalesce(sum(credits_used),0) / nullif(count_if(credits_used>0),0),1) FROM per_user) AS avg_credits_per_active,
          (SELECT count_if(subscription_tier='Pro') FROM per_user) AS tier_pro,
          (SELECT count_if(subscription_tier='ProPlus') FROM per_user) AS tier_proplus,
          (SELECT count_if(subscription_tier='ProMax') FROM per_user) AS tier_promax,
          (SELECT count_if(subscription_tier='Power') FROM per_user) AS tier_power,
          (SELECT round(coalesce(avg(dau),0),1) FROM per_day) AS daily_active_avg,
          (SELECT coalesce(max(dau),0) FROM per_day) AS daily_active_peak
        """
        return QuerySpec(sql=_norm(sql),
                         note="KPI 与档位分布：按用户聚合区间内 credits；活跃=区间内 credits_used>0；超额=区间累计 credits>usage_limit；日活均值/峰值=按天 distinct 活跃用户的区间 avg/max")

    # -- 每日趋势（credits + 活跃用户）----------------------------------------
    def trend(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        SELECT date,
               round(sum(credits_used),1) AS credits,
               count(distinct email) AS active_users
        FROM {self.t_daily}
        {w}
        GROUP BY date
        ORDER BY date
        """
        return QuerySpec(sql=_norm(sql), note="每日 credits 与活跃用户数")

    # -- 模型使用分布 ----------------------------------------------------------
    def models(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        # 模型长表按 email 关联 identity_filter
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        SELECT model, sum(model_messages) AS messages
        FROM {self.t_model}
        {w}
        GROUP BY model
        ORDER BY messages DESC
        """
        return QuerySpec(sql=_norm(sql), note="模型消息数分布（含 auto）")

    # -- 模型集中度明细（每员工目标模型消息占个人全模型消息比例）------------
    def model_concentration_detail(self, models: list[str], date_from=None,
                                   date_to=None, identity_filter=None) -> QuerySpec:
        """每员工 ratio = 目标模型合计消息数 / 个人全模型消息数（口径见只读调研报告）。

        两段式：per_user（按 email 聚合 total/target，HAVING total>0 排除 0 用量用户）
        → ratios（算比例）。返回 email, ratio（保留 4 位），按 ratio 降序。
        模型名经 _q 转义进 IN (...)；白名单校验由 API 层用 model_list() 结果做。
        空 models 抛 ValueError。
        """
        sel = [str(m).strip() for m in (models or []) if str(m).strip()]
        if not sel:
            raise ValueError("models must be a non-empty list")
        in_list = ", ".join(_q(m) for m in sel)
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        WITH per_user AS (
          SELECT email,
                 sum(model_messages) AS total_msgs,
                 sum(CASE WHEN model IN ({in_list}) THEN model_messages ELSE 0 END) AS target_msgs
          FROM {self.t_model}
          {w}
          GROUP BY email
          HAVING sum(model_messages) > 0
        ),
        ratios AS (
          SELECT email, total_msgs, target_msgs,
                 CAST(target_msgs AS double) / total_msgs AS ratio
          FROM per_user
        )
        SELECT email, total_msgs, target_msgs, round(ratio, 4) AS ratio
        FROM ratios
        ORDER BY ratio DESC
        """
        return QuerySpec(sql=_norm(sql),
                         note="模型集中度明细：每员工 目标模型合计消息/个人全模型消息；分母=区间内有消息的员工")

    # -- 全量用户（月度报告用：不分页，含额度/档位/使用率）-------------------
    def report_users(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        """区间内每用户累计 credits + 最近一天档位/额度 + 使用率（月度报告/调档建议）。"""
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        SELECT email,
               max_by(display_name, date) AS display_name,
               max_by(department, date) AS department,
               max_by(subscription_tier, date) AS subscription_tier,
               round(sum(credits_used),2) AS credits_used,
               round(max_by(usage_limit, date),1) AS usage_limit,
               round(sum(credits_used)/nullif(max_by(usage_limit, date),0),4) AS usage_rate
        FROM {self.t_daily}
        {w}
        GROUP BY email
        ORDER BY usage_rate DESC
        """
        return QuerySpec(sql=_norm(sql), note="月度报告：全量用户累计 credits/额度/使用率")

    # -- 全量用户用量（不分页，供与注册名单合并 / CSV 导出）-----------
    def all_users_usage(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        """区间内每用户聚合的完整用量行（与明细表字段一致，但不分页）。

        用于后端把「注册名单（DynamoDB）」与「用量（Athena）」LEFT JOIN：
        以 email 为键，返回所有有用量记录的用户；零用量用户由名单补全。
        字段与 `users()` 明细表对齐（credits_used/usage_limit/balance/usage_rate/
        is_overage/est_cost/total_messages/active_days/档位）。
        """
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        SELECT email,
               max_by(display_name, date) AS display_name,
               max_by(department, date) AS department,
               max_by(subscription_tier, date) AS subscription_tier,
               round(sum(credits_used),2) AS credits_used,
               round(max_by(usage_limit, date),1) AS usage_limit,
               round(max_by(usage_limit, date) - sum(credits_used),2) AS balance,
               round(sum(credits_used)/nullif(max_by(usage_limit, date),0),4) AS usage_rate,
               (max_by(usage_limit, date) > 0 AND sum(credits_used) > max_by(usage_limit, date)) AS is_overage,
               round(sum(est_cost) + {_tier_monthly_sql('max_by(subscription_tier, date)')},2) AS est_cost,
               sum(total_messages) AS total_messages,
               count(distinct date) AS active_days
        FROM {self.t_daily}
        {w}
        GROUP BY email
        """
        return QuerySpec(sql=_norm(sql), note="全量用户用量（不分页，供合并注册名单/CSV 导出）")

    # -- 通用参数化查询（NL Agent 用：一个工具覆盖大多数用量问法）-----------
    def query_usage(self, metric: str = "credits", group_by: str = "none",
                    filters: Optional[dict] = None,
                    date_from=None, date_to=None,
                    sort: str = "desc", limit: int = 50,
                    identity_filter=None) -> QuerySpec:
        """通用结构化查询构造器（全白名单，不接受任意 SQL）。

        metric   : credits | messages | est_cost | active_days | users
        group_by : none | department | subscription_tier | model | date | user
        filters  : {department, subscription_tier, model, user(姓名/邮箱关键词), overage_only(bool)}
        时间/排序/limit 均参数化与校验。

        路径：metric=messages 或 group_by=model 走模型长表（fact_user_model_daily）；
        其余走日表按用户聚合（fact_user_daily）。
        """
        if metric not in _ALLOWED_METRICS:
            raise ValueError(f"invalid metric: {metric!r}")
        if group_by not in _ALLOWED_GROUP_BY:
            raise ValueError(f"invalid group_by: {group_by!r}")
        filters = filters or {}
        direction = "asc" if str(sort).lower() == "asc" else "desc"
        limit = _valid_limit(limit)

        use_model = (metric == "messages" or group_by == "model")

        # 公共过滤（部门/档位/超额/用户关键词）——按可用列分表处理
        # 模型长表只有 email（无 display_name/department），故其用户过滤只能按 email。
        def _common_extra(has_dept_tier: bool, email_only_search: bool = False) -> list[str]:
            ex = []
            dep = filters.get("department")
            if dep and has_dept_tier:
                ex.append(f"department = {_q(str(dep))}")
            tier = filters.get("subscription_tier")
            if tier and has_dept_tier:
                ex.append(f"subscription_tier = {_q(_norm_tier(str(tier)))}")
            model = filters.get("model")
            if model and use_model:
                ex.append(f"model = {_q(str(model))}")
            sc = (_email_search_clause(filters.get("user")) if email_only_search
                  else _search_clause(filters.get("user")))
            if sc:
                ex.append(sc)
            return ex

        if use_model:
            # 模型长表：只有 email，用户过滤按 email（中文名需先在日表解析为 email）
            w = self._where(date_from, date_to, identity_filter,
                            extra=_common_extra(False, email_only_search=True))
            gb = {"model": "model", "user": "email", "date": "date",
                  "none": None}.get(group_by, "model")
            if gb is None:
                sql = f"SELECT sum(model_messages) AS messages FROM {self.t_model} {w}"
            else:
                sql = (f"SELECT {gb} AS {gb}, sum(model_messages) AS messages "
                       f"FROM {self.t_model} {w} GROUP BY {gb} "
                       f"ORDER BY messages {direction} LIMIT {limit}")
            return QuerySpec(sql=_norm(sql),
                             note=f"query_usage: metric=messages group_by={group_by}（模型长表，消息数=提问次数）")

        # 日表：先按用户聚合出每用户指标，再按 group_by 汇总
        extra = _common_extra(True)
        w = self._where(date_from, date_to, identity_filter, extra=extra)
        having = ("HAVING max_by(usage_limit, date) > 0 AND "
                  "sum(credits_used) > max_by(usage_limit, date)") if filters.get("overage_only") else ""

        per_user = (
            f"SELECT email, max_by(display_name, date) AS display_name, "
            f"max_by(department, date) AS department, "
            f"max_by(subscription_tier, date) AS subscription_tier, "
            f"sum(credits_used) AS credits, "
            # est_cost = 月费（按档位）+ overage（sum 日表 est_cost），口径同 summary。
            f"sum(est_cost) + {_tier_monthly_sql('max_by(subscription_tier, date)')} AS est_cost, "
            f"count(distinct date) AS active_days "
            f"FROM {self.t_daily} {w} GROUP BY email {having}"
        )

        gb_col = {"department": "department", "subscription_tier": "subscription_tier",
                  "date": "date", "user": "email"}.get(group_by)

        if group_by == "user" or group_by == "none":
            # 明细/汇总：直接列用户（none 时也按用户列，便于 Agent 读单用户）
            metric_sel = {"credits": "round(credits,1)", "est_cost": "round(est_cost,2)",
                          "active_days": "active_days"}.get(metric, "round(credits,1)")
            sql = (f"SELECT email, display_name, department, subscription_tier, "
                   f"{metric_sel} AS {metric} FROM ({per_user}) "
                   f"ORDER BY {metric} {direction} LIMIT {limit}")
        elif group_by == "date":
            # 按天：需回到原始行（不能用 per_user 的用户聚合）
            magg = {"credits": "round(sum(credits_used),1)", "est_cost": "round(sum(est_cost),2)",
                    "users": "count(distinct email)"}.get(metric, "round(sum(credits_used),1)")
            sql = (f"SELECT date, {magg} AS {metric if metric!='users' else 'users'} "
                   f"FROM {self.t_daily} {w} GROUP BY date ORDER BY date")
        else:
            # 按部门/档位：对 per_user 再聚合
            if metric == "users":
                # 人数：只出一列 users，避免重复列名
                sql = (f"SELECT {gb_col} AS {gb_col}, count(*) AS users "
                       f"FROM ({per_user}) GROUP BY {gb_col} "
                       f"ORDER BY users {direction} LIMIT {limit}")
            else:
                if metric == "active_days":
                    agg = "round(avg(active_days),1)"
                elif metric == "est_cost":
                    agg = "round(sum(est_cost),2)"
                else:
                    agg = "round(sum(credits),1)"
                sql = (f"SELECT {gb_col} AS {gb_col}, {agg} AS {metric}, count(*) AS users "
                       f"FROM ({per_user}) GROUP BY {gb_col} "
                       f"ORDER BY {metric} {direction} LIMIT {limit}")
        return QuerySpec(sql=_norm(sql),
                         note=f"query_usage: metric={metric} group_by={group_by} filters={list(filters)}")

    # -- 部门对比（总 credits + 人均）----------------------------------------
    def departments(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        WITH per_user AS (
          SELECT email, max(department) AS department, sum(credits_used) AS credits_used
          FROM {self.t_daily}
          {w}
          GROUP BY email
        )
        SELECT department,
               count(*) AS users,
               round(sum(credits_used),1) AS total_credits,
               round(sum(credits_used)/nullif(count(*),0),1) AS avg_credits
        FROM per_user
        GROUP BY department
        ORDER BY total_credits DESC
        """
        return QuerySpec(sql=_norm(sql), note="各部门总 credits 与人均 credits")

    # -- Top N 用户 ------------------------------------------------------------
    def top_users(self, date_from=None, date_to=None, limit: int = 15,
                  identity_filter=None) -> QuerySpec:
        limit = _valid_limit(limit)
        w = self._where(date_from, date_to, identity_filter)
        sql = f"""
        SELECT email,
               max(display_name) AS display_name,
               max(department) AS department,
               max_by(subscription_tier, date) AS subscription_tier,
               round(sum(credits_used),2) AS credits_used
        FROM {self.t_daily}
        {w}
        GROUP BY email
        ORDER BY credits_used DESC
        LIMIT {limit}
        """
        return QuerySpec(sql=_norm(sql), note=f"Top {limit} 用户（按区间总 credits）")

    # -- 明细表（分页/排序/部门过滤/姓名搜索）------------------------------
    def users(self, date_from=None, date_to=None, department: Optional[str] = None,
              sort: str = "credits_used", direction: str = "desc",
              page: int = 1, page_size: int = 20, identity_filter=None,
              search: Optional[str] = None) -> QuerySpec:
        sort_col = sort if sort in _SORTABLE_COLUMNS else "credits_used"
        direction = direction.lower() if direction.lower() in _SORT_DIRS else "desc"
        page = max(1, int(page))
        page_size = min(200, max(1, int(page_size)))
        offset = (page - 1) * page_size

        extra = []
        if department:
            extra.append(f"department = {_q(department)}")
        sc = _search_clause(search)
        if sc:
            extra.append(sc)
        w = self._where(date_from, date_to, identity_filter, extra=extra)
        sql = f"""
        SELECT email,
               max(display_name) AS display_name,
               max(department) AS department,
               max_by(subscription_tier, date) AS subscription_tier,
               round(sum(credits_used),2) AS credits_used,
               round(max_by(usage_limit, date),1) AS usage_limit,
               round(greatest(max_by(usage_limit, date) - sum(credits_used), 0),2) AS balance,
               round(sum(credits_used)/nullif(max_by(usage_limit, date),0),4) AS usage_rate,
               (max_by(usage_limit, date) > 0 AND sum(credits_used) > max_by(usage_limit, date)) AS is_overage,
               round(sum(est_cost) + {_tier_monthly_sql('max_by(subscription_tier, date)')},2) AS est_cost,
               sum(total_messages) AS total_messages,
               count(distinct date) AS active_days
        FROM {self.t_daily}
        {w}
        GROUP BY email
        ORDER BY {sort_col} {direction}
        OFFSET {offset}
        LIMIT {page_size}
        """
        return QuerySpec(sql=_norm(sql),
                         note=f"明细表 page={page} size={page_size} sort={sort_col} {direction}")

    def users_count(self, date_from=None, date_to=None, department: Optional[str] = None,
                    identity_filter=None, search: Optional[str] = None) -> QuerySpec:
        extra = []
        if department:
            extra.append(f"department = {_q(department)}")
        sc = _search_clause(search)
        if sc:
            extra.append(sc)
        w = self._where(date_from, date_to, identity_filter, extra=extra)
        sql = f"""
        SELECT count(*) AS total FROM (
          SELECT email FROM {self.t_daily} {w} GROUP BY email
        )
        """
        return QuerySpec(sql=_norm(sql), note="明细表总行数（分页用）")

    # -- 筛选项：可选周期、部门列表、模型列表 --------------------------------
    def periods(self) -> QuerySpec:
        sql = f"SELECT distinct substr(date,1,7) AS period FROM {self.t_daily} ORDER BY period DESC"
        return QuerySpec(sql=_norm(sql), note="可选数据周期(年月)")

    def department_list(self) -> QuerySpec:
        sql = f"SELECT distinct department FROM {self.t_daily} ORDER BY department"
        return QuerySpec(sql=_norm(sql), note="部门列表")

    def model_list(self) -> QuerySpec:
        sql = f"SELECT distinct model FROM {self.t_model} ORDER BY model"
        return QuerySpec(sql=_norm(sql), note="模型列表")

    # -- 超额用户（Agent 常用）------------------------------------------------
    def overage_users(self, date_from=None, date_to=None, identity_filter=None) -> QuerySpec:
        w = self._where(date_from, date_to, identity_filter)
        # 超额定义：区间累计 credits > usage_limit（对齐参照看板 / Kiro 月度额度）。
        # 先按用户聚合，再用 sum(credits)>max(usage_limit) 筛选；overage_credits = 超出额度的部分。
        sql = f"""
        WITH per_user AS (
          SELECT email,
                 max(display_name) AS display_name,
                 max(department) AS department,
                 sum(credits_used) AS credits_used,
                 max_by(usage_limit, date) AS usage_limit
          FROM {self.t_daily}
          {w}
          GROUP BY email
        )
        SELECT email, display_name, department,
               round(greatest(credits_used - usage_limit, 0),1) AS overage_credits,
               round(credits_used,1) AS credits_used,
               round(usage_limit,1) AS usage_limit
        FROM per_user
        WHERE usage_limit > 0 AND credits_used > usage_limit
        ORDER BY overage_credits DESC
        """
        return QuerySpec(sql=_norm(sql), note="触发超额的用户（区间累计 credits>usage_limit）")


def _norm(sql: str) -> str:
    """压缩多余空白，便于日志与缓存 key。"""
    return re.sub(r"\s+", " ", sql).strip()


def _search_clause(search: Optional[str]) -> str:
    """构造按姓名/邮箱模糊搜索的参数化 WHERE 片段（防注入）。

    匹配 lower(display_name) 或 lower(email) 包含关键词。转义 LIKE 通配符 % _ 与单引号。
    空/纯空白返回空串（不加条件）。
    """
    s = (search or "").strip().lower()
    if not s:
        return ""
    # 转义 LIKE 特殊字符，再包上 %...%；_q 再处理单引号
    esc = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pat = _q(f"%{esc}%")
    return (f"(lower(display_name) LIKE {pat} ESCAPE '\\' "
            f"OR lower(email) LIKE {pat} ESCAPE '\\')")


def _email_search_clause(search: Optional[str]) -> str:
    """仅按 email 模糊匹配（用于模型长表，它无 display_name 列）。"""
    s = (search or "").strip().lower()
    if not s:
        return ""
    esc = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pat = _q(f"%{esc}%")
    return f"lower(email) LIKE {pat} ESCAPE '\\'"


def _norm_tier(v: str) -> str:
    """将各种档位写法归一化为表内规范值（Pro/ProPlus/ProMax/Power）。"""
    key = (v or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "").replace("+", "plus")
    return {"pro": "Pro", "proplus": "ProPlus", "promax": "ProMax",
            "power": "Power"}.get(key, v.strip())


def _valid_limit(n: int) -> int:
    n = int(n)
    if n < 1:
        return 1
    return min(n, 500)


# ---------------------------------------------------------------------------
# 执行器：真实 Athena runner + TTL 缓存
# ---------------------------------------------------------------------------
Row = dict[str, Any]
Runner = Callable[[str], list[Row]]


@dataclass
class _CacheEntry:
    ts: float
    rows: list[Row]


class QueryLayer:
    """把 QuerySpec 执行为行列表。runner 可注入以便单测。"""

    def __init__(self, runner: Runner, cache_ttl: float = 60.0):
        self._runner = runner
        self._ttl = cache_ttl
        self._cache: dict[str, _CacheEntry] = {}

    def run(self, spec: QuerySpec) -> list[Row]:
        now = time.time()
        hit = self._cache.get(spec.sql)
        if hit and (now - hit.ts) < self._ttl:
            return hit.rows
        rows = self._runner(spec.sql)
        self._cache[spec.sql] = _CacheEntry(ts=now, rows=rows)
        return rows

    def one(self, spec: QuerySpec) -> Row:
        rows = self.run(spec)
        return rows[0] if rows else {}


def make_athena_runner(*, database: str, workgroup: str, region: str,
                       poll_interval: float = 0.5, timeout: float = 45.0) -> Runner:
    """构造一个真实 Athena runner（boto3）。在 Lambda/本地凭证下可用。"""
    import logging
    import boto3  # 延迟导入，便于单测不依赖 boto3

    log = logging.getLogger("athena")
    client = boto3.client("athena", region_name=region)

    def _run(sql: str) -> list[Row]:
        try:
            qid = client.start_query_execution(
                QueryString=sql,
                QueryExecutionContext={"Database": database},
                WorkGroup=workgroup,
            )["QueryExecutionId"]
        except Exception:
            # 不记录 SQL 文本：查询里以字面量嵌入了用户邮箱等个人信息，只记长度。
            log.exception("start_query_execution failed; wg=%s db=%s sql_len=%d",
                          workgroup, database, len(sql))
            raise

        deadline = time.time() + timeout
        while True:
            st = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
            state = st["State"]
            if state == "SUCCEEDED":
                break
            if state in ("FAILED", "CANCELLED"):
                reason = st.get("StateChangeReason", "")
                log.error("Athena %s qid=%s reason=%s", state, qid, reason)
                raise RuntimeError(f"Athena query {state}: {reason}")
            if time.time() > deadline:
                raise TimeoutError(f"Athena query timed out after {timeout}s (qid={qid})")
            _POLL_WAIT.wait(poll_interval)

        return _collect_results(client, qid)

    return _run


_POLL_WAIT = threading.Event()  # never set; .wait(t) is the poll delay


def _collect_results(client, qid: str) -> list[Row]:
    """把 get_query_results 的分页结果转为 [{col: typed_value}]。"""
    rows: list[Row] = []
    header: list[str] = []
    token = None
    first = True
    while True:
        kw = {"QueryExecutionId": qid}
        if token:
            kw["NextToken"] = token
        resp = client.get_query_results(**kw)
        rs = resp["ResultSet"]
        col_info = rs["ResultSetMetadata"]["ColumnInfo"]
        types = [c["Type"] for c in col_info]
        for i, r in enumerate(rs["Rows"]):
            values = [d.get("VarCharValue") for d in r["Data"]]
            if first and i == 0:
                header = [c["Name"] for c in col_info]
                continue
            rows.append({header[j]: _coerce(values[j], types[j]) for j in range(len(header))})
        first = False
        token = resp.get("NextToken")
        if not token:
            break
    return rows


def _coerce(v: Optional[str], t: str) -> Any:
    if v is None:
        return None
    if t in ("bigint", "integer", "int", "smallint", "tinyint"):
        try:
            return int(v)
        except ValueError:
            return None
    if t in ("double", "float", "decimal", "real"):
        try:
            return float(v)
        except ValueError:
            return None
    if t == "boolean":
        return v.lower() == "true"
    return v
