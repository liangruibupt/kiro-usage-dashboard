"""FastAPI endpoint 单测：用 dependency_overrides 注入 mock 查询层，不触碰 AWS。

MockLayer 按 SQL 里出现的表/关键字返回对应 canned 行，覆盖 7 个 endpoint 的响应形状。
"""
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.deps import get_query_layer
from backend.shared.athena_client import QuerySpec


class MockLayer:
    """根据 QuerySpec.note/sql 关键字返回 canned 结果。"""

    def run(self, spec: QuerySpec):
        s = spec.sql
        if "GROUP BY date" in s:  # trend
            return [
                {"date": "2026-09-01", "credits": 100.0, "active_users": 10},
                {"date": "2026-09-02", "credits": 120.5, "active_users": 12},
            ]
        if "fact_user_model_daily" in s and "GROUP BY model" in s:
            return [
                {"model": "claude_haiku_4_5", "messages": 13036},
                {"model": "auto", "messages": 12237},
            ]
        if "GROUP BY department" in s:
            return [
                {"department": "研发中心", "users": 10, "total_credits": 4403.4, "avg_credits": 440.3},
                {"department": "未分组", "users": 5, "total_credits": 1998.7, "avg_credits": 399.7},
            ]
        if "ORDER BY credits_used DESC" in s and "LIMIT" in s:  # top-users
            return [
                {"email": "u1@x.com", "display_name": "梁伟", "department": "研发中心",
                 "subscription_tier": "Power", "credits_used": 250.8},
            ]
        if "ORDER BY usage_rate" in s:  # report_users
            return [
                {"email": "hi@x.com", "display_name": "高用", "department": "阳光云",
                 "subscription_tier": "Pro", "credits_used": 1500.0, "usage_limit": 1000.0, "usage_rate": 1.5},
                {"email": "lo@x.com", "display_name": "低用", "department": "阳光云",
                 "subscription_tier": "ProMax", "credits_used": 500.0, "usage_limit": 5000.0, "usage_rate": 0.10},
                {"email": "mid@x.com", "display_name": "中用", "department": "阳光云",
                 "subscription_tier": "ProPlus", "credits_used": 1200.0, "usage_limit": 2000.0, "usage_rate": 0.60},
            ]
        if "OFFSET" in s:  # users detail page
            return [
                {"email": "u1@x.com", "display_name": "梁伟", "department": "研发中心",
                 "subscription_tier": "Power", "credits_used": 250.8, "usage_limit": 400.0,
                 "balance": 149.2, "usage_rate": 0.627, "is_overage": False, "est_cost": 10.03,
                 "total_messages": 916, "active_days": 5},
            ]
        if "substr(date,1,7)" in s:  # periods
            return [{"period": "2026-09"}]
        if "distinct department" in s:
            return [{"department": "研发中心"}, {"department": "未分组"}]
        if "distinct model" in s:
            return [{"model": "auto"}, {"model": "gpt_5"}]
        return []

    def one(self, spec: QuerySpec):
        s = spec.sql
        if "tier_power" in s:  # summary
            return {
                "total_users": 40, "active_users": 36, "total_credits": 21374.2,
                "est_cost": 3206.13, "overage_users": 2, "avg_credits_per_active": 593.7,
                "tier_pro": 4, "tier_proplus": 4, "tier_promax": 9, "tier_power": 23,
                "daily_active_avg": 18.5, "daily_active_peak": 24,
            }
        if "count(*) AS total FROM" in s:  # users_count
            return {"total": 40}
        rows = self.run(spec)
        return rows[0] if rows else {}


@pytest.fixture
def client():
    app.dependency_overrides[get_query_layer] = lambda: MockLayer()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_summary(client):
    r = client.get("/api/summary?period=2026-09")
    assert r.status_code == 200
    body = r.json()
    assert body["kpis"]["total_users"] == 40
    assert body["kpis"]["active_users"] == 36
    assert body["kpis"]["activation_rate"] == round(36 / 40, 4)
    assert body["tiers"]["Power"] == 23
    assert body["range"]["from"] == "2026-09-01"
    assert "$/credit" in body["est_cost_note"]


def test_trend(client):
    r = client.get("/api/trend?from=2026-09-01&to=2026-09-07")
    assert r.status_code == 200
    s = r.json()["series"]
    assert len(s) == 2 and s[0]["date"] == "2026-09-01" and s[1]["active_users"] == 12


def test_models(client):
    r = client.get("/api/models")
    assert r.status_code == 200
    m = r.json()["models"]
    assert m[0]["model"] == "claude_haiku_4_5" and m[0]["messages"] == 13036


def test_departments(client):
    r = client.get("/api/departments")
    assert r.status_code == 200
    d = r.json()["departments"]
    assert d[0]["department"] == "研发中心" and d[0]["users"] == 10


def test_top_users_default_limit(client):
    r = client.get("/api/top-users")
    assert r.status_code == 200
    assert r.json()["users"][0]["email"] == "u1@x.com"


def test_users_detail(client):
    r = client.get("/api/users?page=1&page_size=20&sort=credits_used")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 40 and body["page"] == 1
    item = body["items"][0]
    assert item["email"] == "u1@x.com"
    assert item["status"] == "normal"  # usage_rate 0.627 -> normal


def test_filters(client):
    r = client.get("/api/filters")
    assert r.status_code == 200
    body = r.json()
    assert body["periods"] == ["2026-09"]
    assert "未分组" in body["departments"]
    assert "auto" in body["models"]


def test_users_invalid_sort_is_accepted_and_sanitized(client):
    # 非白名单 sort 不应 500（查询层回退到 credits_used）
    r = client.get("/api/users?sort=DROP+TABLE")
    assert r.status_code == 200


def test_summary_bad_period_rejected(client):
    r = client.get("/api/summary?period=2026-9")  # 不符合 YYYY-MM
    assert r.status_code == 422


def test_report(client):
    r = client.get("/api/report?period=2026-09")
    assert r.status_code == 200
    body = r.json()
    # 汇总 KPI
    assert body["kpis"]["active_users"] == 36
    assert body["kpis"]["tiers"]["Power"] == 23
    # 套餐匹配：高用(Pro,1500>1000)->ProPlus；低用(ProMax,500仅需Pro)->Pro（跨多档降）；中用(ProPlus,1200落在Pro+)保持
    ups = {u["email"]: u["suggested_tier"] for u in body["upgrades"]}
    downs = {d["email"]: d["suggested_tier"] for d in body["downgrades"]}
    assert ups == {"hi@x.com": "ProPlus"}
    assert downs == {"lo@x.com": "Pro"}


# --- 组级授权中间件（R2/R4）------------------------------------------------
def _req_with_claims(groups):
    """构造一个带 API Gateway JWT authorizer 上下文的 ASGI scope 扩展。

    TestClient 无法直接注入 scope['aws.event']，改用 httpx + 自定义 transport 不便；
    这里直接单测中间件逻辑覆盖的关键分支：有 event 且缺组 -> 403。
    """
    return {
        "requestContext": {"authorizer": {"jwt": {"claims": {
            "cognito:groups": groups}}}}}


def test_group_enforcement_forbids_non_admin(monkeypatch):
    """带 authorizer 上下文但无 admins 组 -> 403。"""
    from starlette.testclient import TestClient as _TC
    app.dependency_overrides[get_query_layer] = lambda: MockLayer()

    # 用 raw ASGI 调用，注入 aws.event 到 scope
    import asyncio

    async def call(groups_value):
        from backend.api.main import app as fastapi_app
        scope = {
            "type": "http", "method": "GET", "path": "/api/summary",
            "raw_path": b"/api/summary", "query_string": b"period=2026-09",
            "headers": [], "aws.event": {"requestContext": {"authorizer": {
                "jwt": {"claims": {"cognito:groups": groups_value}}}}},
            "scheme": "http", "server": ("test", 80), "client": ("t", 1),
            "http_version": "1.1",
        }
        messages = []
        received = {"done": False}

        async def receive():
            if not received["done"]:
                received["done"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(msg):
            messages.append(msg)

        await fastapi_app(scope, receive, send)
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        return status

    try:
        assert asyncio.run(call("users")) == 403        # 非 admins
        assert asyncio.run(call("admins")) == 200        # admins 放行
        assert asyncio.run(call("admins engineers")) == 200  # 多组含 admins
        # HTTP API JWT authorizer 注入的带方括号字符串格式
        assert asyncio.run(call("[admins]")) == 200
        assert asyncio.run(call("[users engineers]")) == 403
        assert asyncio.run(call("[admins engineers]")) == 200
        # 原始 list 格式
        assert asyncio.run(call(["admins"])) == 200
        assert asyncio.run(call(["users"])) == 403
    finally:
        app.dependency_overrides.clear()


def test_parse_groups_formats():
    from backend.api.main import _parse_groups
    assert _parse_groups(None) == set()
    assert _parse_groups(["admins", "x"]) == {"admins", "x"}
    assert _parse_groups("[admins engineers]") == {"admins", "engineers"}
    assert _parse_groups("admins,engineers") == {"admins", "engineers"}
    assert _parse_groups("[admins]") == {"admins"}
