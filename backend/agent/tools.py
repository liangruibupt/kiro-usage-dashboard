"""NL 问答 Agent 的受控查询工具（复用共享查询层）。

设计（通用方案）：
- 主力工具是 **query_usage**：一个参数化的通用查询，用白名单维度覆盖绝大多数
  用量问法（某指标 × 某分组 × 某过滤 × 某时间段），不再一问题一工具、不接受任意 SQL。
- 辅助工具：find_user（把中文人名解析为 email）、get_summary（KPI 概览）、get_filters（可选值）。
- 每个工具带 identity_filter 兜底钩子；v1 管理员=None（全量），v2 由上层注入。

打包：容器内把 backend/shared/{athena_client,derivations}.py 平铺到同级，
故这里用 `from athena_client import ...` 的扁平 import（本地开发用 sys.path 兜底）。
"""
from __future__ import annotations

import os
from typing import Optional

from strands import tool

try:
    from athena_client import Queries, QueryLayer, make_athena_runner
except ImportError:  # 本地开发（仓库根）
    from backend.shared.athena_client import Queries, QueryLayer, make_athena_runner  # type: ignore

GLUE_DB = os.environ.get("GLUE_DB", "kiro_usage")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "KiroDash-Data-wg")
REGION = os.environ.get("AWS_REGION", os.environ.get("DEPLOY_REGION", "us-west-2"))

# v1：管理员全量，identity_filter=None。v2 由上层注入。
IDENTITY_FILTER: Optional[dict] = None

_queries = Queries(db=GLUE_DB)
_layer: Optional[QueryLayer] = None


def _get_layer() -> QueryLayer:
    global _layer
    if _layer is None:
        runner = make_athena_runner(
            database=GLUE_DB, workgroup=ATHENA_WORKGROUP, region=REGION)
        _layer = QueryLayer(runner, cache_ttl=60)
    return _layer


def _resolve(period, df, dt):
    """period=YYYY-MM 展开为月首末日；否则用 date_from/date_to。"""
    if period and not (df or dt):
        return f"{period}-01", f"{period}-31"
    return df, dt


@tool
def query_usage(metric: str = "credits", group_by: str = "none",
                department: Optional[str] = None,
                subscription_tier: Optional[str] = None,
                model: Optional[str] = None,
                user: Optional[str] = None,
                overage_only: bool = False,
                period: Optional[str] = None,
                date_from: Optional[str] = None,
                date_to: Optional[str] = None,
                sort: str = "desc", limit: int = 50) -> dict:
    """通用用量查询：一个工具回答绝大多数用量问题。按【指标 × 分组 × 过滤 × 时间段】组合。

    用法示例（把自然语言映射到参数）：
    - "哪个部门 Credits 最高" -> metric=credits, group_by=department
    - "各档位分别多少人" -> metric=users, group_by=subscription_tier
    - "Credits 最高的 10 个用户" -> metric=credits, group_by=user, limit=10
    - "王磊用了哪些模型/各模型用量" -> metric=messages, group_by=model, user=<王磊的email>
      （若只知道中文名，先用 find_user 拿到 email 再填 user）
    - "整体模型分布" -> metric=messages, group_by=model
    - "谁超额了" -> metric=credits, group_by=user, overage_only=true
    - "阳光云本月 Credits" -> metric=credits, group_by=none/user, department=阳光云
    - "每日 Credits 趋势" -> metric=credits, group_by=date

    Args:
        metric: 指标。可选 credits | messages(模型消息数=提问次数) | est_cost | active_days | users(人数)。
        group_by: 分组维度。可选 none | department | subscription_tier | model | date | user。
        department: 按部门过滤（中文部门名，如 阳光云）。
        subscription_tier: 按档位过滤（Pro/Pro+/Pro Max/Power）。
        model: 按模型过滤（如 gpt_5.6_sol）。
        user: 按用户过滤（邮箱或姓名关键词；模型相关查询只按邮箱匹配，姓名请先用 find_user 解析）。
        overage_only: 仅返回超额用户（区间累计 credits > 套餐额度）。
        period: 数据周期 YYYY-MM（与 date_from/date_to 二选一）。
        date_from: 起始日期 YYYY-MM-DD。
        date_to: 结束日期 YYYY-MM-DD。
        sort: 排序方向 asc | desc（默认 desc）。
        limit: 返回条数（默认 50，最大 500）。
    """
    df, dt = _resolve(period, date_from, date_to)
    filters = {"department": department, "subscription_tier": subscription_tier,
               "model": model, "user": user, "overage_only": overage_only}
    filters = {k: v for k, v in filters.items() if v}
    spec = _queries.query_usage(
        metric=metric, group_by=group_by, filters=filters,
        date_from=df, date_to=dt, sort=sort, limit=limit,
        identity_filter=IDENTITY_FILTER)
    rows = _get_layer().run(spec)
    return {"range": {"from": df, "to": dt}, "metric": metric, "group_by": group_by,
            "rows": rows, "note": spec.note}


@tool
def find_user(keyword: str) -> dict:
    """按姓名或邮箱关键词查找用户（全时段搜人，不受月份限制），返回其邮箱/部门/档位。

    用于把"王磊"这类中文名解析成具体 email——查某人的模型/用量时，先用本工具拿到
    email，再调 query_usage(..., user=<email>)。找人与时间无关，故不传日期。

    Args:
        keyword: 姓名或邮箱关键词（如 王磊 或 wanglei）。
    """
    spec = _queries.users(None, None, page=1, page_size=50,
                          identity_filter=IDENTITY_FILTER, search=keyword)
    rows = _get_layer().run(spec)
    users = [{"email": r.get("email"), "display_name": r.get("display_name"),
              "department": r.get("department"), "subscription_tier": r.get("subscription_tier")} for r in rows]
    return {"matched": len(users), "users": users,
            "note": "全时段按姓名/邮箱模糊匹配；多个结果时向用户确认是哪一位"}


@tool
def get_summary(period: Optional[str] = None, date_from: Optional[str] = None,
                date_to: Optional[str] = None) -> dict:
    """获取整体用量 KPI 汇总（注册/活跃用户、激活率、总 Credits、超额用户数、预估成本、档位分布、日活均峰）。

    Args:
        period: 数据周期 YYYY-MM（与 date_from/date_to 二选一）。
        date_from: 起始日期 YYYY-MM-DD。
        date_to: 结束日期 YYYY-MM-DD。
    """
    df, dt = _resolve(period, date_from, date_to)
    spec = _queries.summary(df, dt, identity_filter=IDENTITY_FILTER)
    row = _get_layer().one(spec)
    return {"range": {"from": df, "to": dt}, "kpis": row, "note": spec.note}


@tool
def get_filters() -> dict:
    """获取可用的数据周期、部门、模型列表（用于澄清问题或校验过滤值）。"""
    periods = [r["period"] for r in _get_layer().run(_queries.periods())]
    depts = [r["department"] for r in _get_layer().run(_queries.department_list())]
    models = [r["model"] for r in _get_layer().run(_queries.model_list())]
    return {"periods": periods, "departments": depts, "models": models}


ALL_TOOLS = [query_usage, find_user, get_summary, get_filters]
