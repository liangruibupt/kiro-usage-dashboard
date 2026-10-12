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
        if "ORDER BY ratio DESC" in s:  # model_concentration_detail
            return [
                {"email": "a@x.com", "ratio": 0.9, "target_msgs": 90, "total_msgs": 100},
                {"email": "b@x.com", "ratio": 0.6, "target_msgs": 60, "total_msgs": 100},
                {"email": "c@x.com", "ratio": 0.2, "target_msgs": 20, "total_msgs": 100},
                {"email": "d@x.com", "ratio": 0.0, "target_msgs": 0, "total_msgs": 100},
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
        if "GROUP BY email" in s:  # all_users_usage（合并名单/CSV 导出用）
            return [
                {"email": "u1@x.com", "display_name": "梁伟", "department": "研发中心",
                 "subscription_tier": "Power", "credits_used": 250.8, "usage_limit": 400.0,
                 "balance": 149.2, "usage_rate": 0.627, "is_overage": False, "est_cost": 10.03,
                 "total_messages": 916, "active_days": 5},
                {"email": "u2@x.com", "display_name": "张三", "department": "研发中心",
                 "subscription_tier": "Pro", "credits_used": 120.0, "usage_limit": 400.0,
                 "balance": 280.0, "usage_rate": 0.30, "is_overage": False, "est_cost": 4.8,
                 "total_messages": 300, "active_days": 3},
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
    # 成本说明（方案 A）：月费 + 超额，非账单
    assert "月费" in body["est_cost_note"]
    assert "$0.04/credit" in body["est_cost_note"]


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


def test_model_concentration_ok(client):
    # 合法模型（auto 在 distinct model 白名单内），阈值 0.5
    r = client.get("/api/model-concentration?models=auto&threshold=0.5&bins=10")
    assert r.status_code == 200
    body = r.json()
    assert body["models"] == ["auto"]
    assert body["total_users"] == 4            # 明细 4 行
    assert body["concentrated_users"] == 2     # ratio>=0.5: 0.9, 0.6
    assert body["concentration_rate"] == round(2 / 4, 4)
    assert body["ratios"] == [0.9, 0.6, 0.2, 0.0]
    assert len(body["histogram"]) == 10
    # 0.9->桶9, 0.6->桶6, 0.2->桶2, 0.0->桶0
    counts = {b["from"]: b["count"] for b in body["histogram"]}
    assert counts[0.9] == 1 and counts[0.6] == 1 and counts[0.2] == 1 and counts[0.0] == 1


def test_model_concentration_threshold_counts(client):
    # 阈值 0.6（含等号）：0.9, 0.6 命中 -> 2
    r = client.get("/api/model-concentration?models=auto&threshold=0.6")
    assert r.status_code == 200
    assert r.json()["concentrated_users"] == 2


def test_model_concentration_invalid_model_400(client):
    r = client.get("/api/model-concentration?models=not_a_model")
    assert r.status_code == 400
    body = r.json()
    assert body["detail"] == "no valid model selected"
    assert "auto" in body["valid_models"]


def test_model_concentration_members_shape(client):
    # members 返回全量有消息员工（非只超阈），按 ratio 降序，键齐全
    r = client.get("/api/model-concentration?models=auto&threshold=0.5")
    assert r.status_code == 200
    members = r.json()["members"]
    assert isinstance(members, list) and len(members) == 4   # 全量 4 行
    keys = {"email", "display_name", "department",
            "target_messages", "total_messages", "ratio"}
    for m in members:
        assert keys <= set(m.keys())
    assert [m["ratio"] for m in members] == [0.9, 0.6, 0.2, 0.0]
    # 键名映射：target_messages/total_messages 来自查询层 target_msgs/total_msgs
    assert members[0]["target_messages"] == 90
    assert members[0]["total_messages"] == 100


def test_model_concentration_member_roster_fallback(client):
    # 测试环境未配 DEPT_TABLE → _roster 返回 []，全部走回退：
    # display_name=email 前缀，department="未分组"
    r = client.get("/api/model-concentration?models=auto&threshold=0.5")
    assert r.status_code == 200
    members = r.json()["members"]
    assert members[0]["display_name"] == "a"
    assert members[0]["department"] == "未分组"


def test_model_concentration_member_roster_hit(client, monkeypatch):
    # monkeypatch _roster：命中的 email 取名单姓名/部门，其余回退
    import backend.api.main as main
    monkeypatch.setattr(main, "_roster", lambda settings: [
        {"email": "a@x.com", "display_name": "张伟", "department": "研发中心"},
    ])
    r = client.get("/api/model-concentration?models=auto&threshold=0.5")
    assert r.status_code == 200
    members = r.json()["members"]
    assert members[0]["display_name"] == "张伟"
    assert members[0]["department"] == "研发中心"
    # 未命中的仍回退
    assert members[1]["display_name"] == "b"
    assert members[1]["department"] == "未分组"


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
    # include_all=false 走旧行为（Athena 分页，仅有用量用户）
    r = client.get("/api/users?page=1&page_size=20&sort=credits_used&include_all=false")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 40 and body["page"] == 1
    item = body["items"][0]
    assert item["email"] == "u1@x.com"
    assert item["status"] == "normal"  # usage_rate 0.627 -> normal


def test_users_detail_include_all_default(client):
    # 默认 include_all=true：以名单为基准合并用量（测试环境未配名单表
    # → 降级为仅用量用户）。此路径在内存排序/分页。
    r = client.get("/api/users?page=1&page_size=20&sort=credits_used")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2  # all_users_usage 返回 2 行
    emails = [it["email"] for it in body["items"]]
    assert emails == ["u1@x.com", "u2@x.com"]  # 按 credits_used desc


def test_users_include_out_of_roster_usage_users(client, monkeypatch):
    """D3 口径：注册名单外、但有用量的用户也应纳入明细（部门=未分组）。

    名单(_roster)只有 u1；Athena 用量(all_users_usage)有 u1+u2。u2 不在名单里，
    但真实有用量，必须出现在明细表，且 department 归「未分组」、档位/额度用 Athena 值。
    """
    import backend.api.main as main
    monkeypatch.setattr(main, "_roster", lambda settings: [
        {"email": "u1@x.com", "display_name": "梁伟", "department": "研发中心"},
    ])
    r = client.get("/api/users?page=1&page_size=20&sort=credits_used")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2  # u1(名单内) + u2(名单外有用量) 并集
    by_email = {it["email"]: it for it in body["items"]}
    assert "u2@x.com" in by_email  # 名单外用量用户被纳入
    u2 = by_email["u2@x.com"]
    assert u2["department"] == "未分组"          # 名单外统一归未分组
    assert u2["subscription_tier"] == "Pro"      # 档位用 Athena 真实值
    assert u2["usage_limit"] == 400.0            # 额度用 Athena 真实值


def test_status_tiny_usage_is_normal_not_unused():
    """极少量用量（usage_rate 被 round 归零，但真实用过）应标 normal，而非 unused。

    例：Pro 档消耗 0.04 credit → usage_rate≈0.00004 → round(,4)=0。只看 usage_rate 会
    误判「未使用」；has_usage=True（credits>0 或 messages>0）时应为「正常」。
    """
    import backend.api.main as main
    # 真实用过但 usage_rate 被归零 → 正常
    assert main._status(0.0, is_overage=False, has_usage=True) == "normal"
    # 完全未使用 → 未使用
    assert main._status(0.0, is_overage=False, has_usage=False) == "unused"
    # 超额 / 接近上限 不受影响
    assert main._status(1.05, has_usage=True) == "overage"
    assert main._status(0.85, has_usage=True) == "near_limit"


def test_users_export_csv(client):
    r = client.get("/api/users/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig")  # 剥离 BOM
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0].startswith("邮箱,姓名,部门,档位")
    assert len(lines) == 3  # 表头 + 2 条数据
    assert "u1@x.com" in text and "u2@x.com" in text


def test_users_export_filename_has_dept_and_range(client):
    import urllib.parse

    r = client.get("/api/users/export?department=研发中心&from=2026-09-01&to=2026-09-30")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    # 真实文件名（含中文部门）在 filename*=UTF-8'' 里，需解码后比对
    import re
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd)
    assert m, cd
    decoded = urllib.parse.unquote(m.group(1))
    assert decoded == "kiro-users-研发中心-20260901-20260930.csv", decoded


def test_users_export_filename_no_range_falls_back(client):
    import re
    import urllib.parse

    r = client.get("/api/users/export")  # 无部门、无区间
    cd = r.headers["content-disposition"]
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd)
    assert m, cd
    decoded = urllib.parse.unquote(m.group(1))
    # 部门回退为“全部”，区间回退为“截至YYYYMMDD”
    assert decoded.startswith("kiro-users-全部-截至") and decoded.endswith(".csv"), decoded


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
    # 使用率阈值模型：全量 users[] 带升/降一档建议（suggested_up/suggested_down）
    users = body["users"]
    assert len(users) == 3
    by_email = {u["email"]: u for u in users}
    assert by_email["hi@x.com"]["suggested_up"] == "ProPlus"
    assert by_email["hi@x.com"]["suggested_down"] is None   # Pro 不可再降
    assert by_email["lo@x.com"]["suggested_up"] == "Power"
    assert by_email["lo@x.com"]["suggested_down"] == "ProPlus"
    assert by_email["mid@x.com"]["suggested_up"] == "ProMax"
    assert by_email["mid@x.com"]["suggested_down"] == "Pro"
    # 每行含 usage_rate（前端本地阈值过滤用）
    for u in users:
        assert "usage_rate" in u


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
