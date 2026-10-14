"""共享查询层单测：不触碰真实 AWS。

策略：
- 校验 QuerySpec.sql 的结构（正确的表、GROUP BY、注入的 WHERE、参数化 LIMIT/OFFSET）。
- 校验注入防护：非法 date/period 抛错；单引号被转义；非法 sort 列回退。
- 校验 QueryLayer 的执行 + TTL 缓存（runner 只被调用一次）。
"""
import pytest

from backend.shared.athena_client import (
    Queries, QueryLayer, QuerySpec, _coerce, _q,
)


@pytest.fixture
def q():
    return Queries()


# --- SQL 构造 --------------------------------------------------------------
def test_summary_sql_shape(q):
    spec = q.summary(date_from="2026-09-01", date_to="2026-09-07")
    s = spec.sql
    assert "kiro_usage.fact_user_daily" in s
    assert "date >= '2026-09-01'" in s
    assert "date <= '2026-09-07'" in s
    assert "tier_power" in s and "overage_users" in s


def test_trend_groups_by_date(q):
    s = q.trend(date_from="2026-09-01", date_to="2026-09-07").sql
    assert "GROUP BY date" in s and "ORDER BY date" in s


def test_models_uses_model_table(q):
    s = q.models().sql
    assert "kiro_usage.fact_user_model_daily" in s
    assert "GROUP BY model" in s


def test_top_users_limit_param(q):
    s = q.top_users(limit=15).sql
    assert "LIMIT 15" in s
    # 上限保护
    assert "LIMIT 500" in q.top_users(limit=99999).sql
    assert "LIMIT 1" in q.top_users(limit=0).sql


def test_users_pagination_and_sort(q):
    s = q.users(page=3, page_size=20, sort="balance", direction="asc").sql
    assert "OFFSET 40" in s      # (3-1)*20
    assert "LIMIT 20" in s
    assert "ORDER BY balance asc" in s


def test_users_invalid_sort_falls_back(q):
    s = q.users(sort="DROP TABLE", direction="weird").sql
    assert "ORDER BY credits_used desc" in s
    assert "DROP TABLE" not in s


def test_users_department_filter_escaped(q):
    s = q.users(department="R&D's team").sql
    assert "department = 'R&D''s team'" in s


def test_model_concentration_detail_shape(q):
    s = q.model_concentration_detail(
        ["auto", "claude_opus_5"], date_from="2026-09-01", date_to="2026-09-07").sql
    assert "kiro_usage.fact_user_model_daily" in s
    assert "GROUP BY email" in s
    assert "HAVING sum(model_messages) > 0" in s
    assert "IN ('auto', 'claude_opus_5')" in s
    assert "ORDER BY ratio DESC" in s
    assert "date >= '2026-09-01'" in s
    # 新增列：total_msgs/target_msgs 透传到 ratios CTE 与最终 SELECT
    assert "total_msgs" in s and "target_msgs" in s
    assert "round(ratio, 4) AS ratio" in s


def test_model_concentration_detail_escapes_quotes(q):
    s = q.model_concentration_detail(["a'b"]).sql
    assert "IN ('a''b')" in s


def test_model_concentration_detail_empty_models_raises(q):
    with pytest.raises(ValueError):
        q.model_concentration_detail([])
    with pytest.raises(ValueError):
        q.model_concentration_detail(["   "])


def test_model_concentration_detail_invalid_date_raises(q):
    with pytest.raises(ValueError):
        q.model_concentration_detail(["auto"], date_from="2026/09/01")


def test_department_and_filter_lists(q):
    assert "distinct department" in q.department_list().sql
    assert "distinct model" in q.model_list().sql
    assert "substr(date,1,7)" in q.periods().sql


# --- 注入防护 --------------------------------------------------------------
def test_invalid_date_raises(q):
    with pytest.raises(ValueError):
        q.summary(date_from="2026/09/01")
    with pytest.raises(ValueError):
        q.trend(date_to="not-a-date")


def test_query_usage_rejects_invalid_metric_and_group_by(q):
    with pytest.raises(ValueError):
        q.query_usage(metric="credits); DROP")
    with pytest.raises(ValueError):
        q.query_usage(metric="credits", group_by="email; DROP")


def test_query_usage_accepts_all_allowed_combinations(q):
    for m in ("credits", "messages", "est_cost", "active_days", "users"):
        for g in ("none", "department", "subscription_tier", "model", "date", "user"):
            assert q.query_usage(metric=m, group_by=g).sql.startswith("SELECT")


def test_identity_filter_injected(q):
    s = q.summary(identity_filter={"email": "Admin@Ex.com"}).sql
    assert "lower(email) = 'admin@ex.com'" in s


def test_identity_filter_none_no_where(q):
    s = q.trend().sql  # 无任何过滤
    assert "WHERE" not in s


def test_quote_escaping():
    assert _q("a'b") == "'a''b'"


# --- 执行 + 缓存 -----------------------------------------------------------
def test_query_layer_runs_and_caches():
    calls = {"n": 0}

    def runner(sql):
        calls["n"] += 1
        return [{"total_users": 40}]

    layer = QueryLayer(runner, cache_ttl=60)
    spec = QuerySpec(sql="SELECT 1")
    r1 = layer.run(spec)
    r2 = layer.run(spec)
    assert r1 == r2 == [{"total_users": 40}]
    assert calls["n"] == 1  # 第二次命中缓存


def test_query_layer_cache_expiry():
    calls = {"n": 0}

    def runner(sql):
        calls["n"] += 1
        return [{"x": calls["n"]}]

    layer = QueryLayer(runner, cache_ttl=0)  # 立即过期
    spec = QuerySpec(sql="SELECT 1")
    layer.run(spec)
    layer.run(spec)
    assert calls["n"] == 2


def test_one_returns_first_or_empty():
    layer = QueryLayer(lambda sql: [], cache_ttl=0)
    assert layer.one(QuerySpec(sql="x")) == {}
    layer2 = QueryLayer(lambda sql: [{"a": 1}, {"a": 2}], cache_ttl=0)
    assert layer2.one(QuerySpec(sql="y")) == {"a": 1}


# --- 类型转换 --------------------------------------------------------------
def test_coerce_types():
    assert _coerce("42", "bigint") == 42
    assert _coerce("3.5", "double") == 3.5
    assert _coerce("true", "boolean") is True
    assert _coerce("false", "boolean") is False
    assert _coerce(None, "bigint") is None
    assert _coerce("abc", "varchar") == "abc"
