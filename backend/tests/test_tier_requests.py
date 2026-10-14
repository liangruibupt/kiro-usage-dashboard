"""档位申请（tier-requests）后端单测：不触碰 AWS。

镜像 test_api.py / test_recommendations.py 的 fake 层风格：
- FakeTable 顶替 DynamoDB Table（get_item/put_item/update_item/scan）；
- FakeSes 顶替 boto3.client("ses")，捕获 send_email 调用；
- get_settings 用 app.dependency_overrides 注入非空表名/api key/发件人；
- TestClient(app) 无 aws.event，main.py 中间件跳过组校验，管理端可达。

覆盖任务 9.9–9.13（R53/R58/R59/R60/R61/R62/R63/R66/R67/R68/R69/R70/R74）。
"""
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api import tier_requests as tr
from backend.api.settings import Settings, get_settings


API_KEY = "test-secret-key"
SENDER = "dash@example.com"


def _settings() -> Settings:
    """一组测试用配置：表名/api key/发件人均非空。"""
    return Settings(
        tier_requests_table="TierRequests-test",
        tier_api_key=API_KEY,
        ses_sender=SENDER,
    )


# --- fake DynamoDB Table -----------------------------------------------------
class FakeTable:
    """内存版 DynamoDB Table，主键为 `id`。

    支持 submit/list/close 用到的 get_item/put_item/update_item/scan。
    scan 若带 supersede 的 requester_email+status 过滤（:e/:p），按之过滤，
    否则返回全部（供 list_requests 全表扫描）。
    """

    def __init__(self, items=None):
        # id -> item dict
        self.items = {it["id"]: dict(it) for it in (items or [])}
        self.put_calls = []
        self.update_calls = []

    def get_item(self, Key):
        item = self.items.get(Key["id"])
        return {"Item": dict(item)} if item is not None else {}

    def put_item(self, Item):
        self.put_calls.append(dict(Item))
        self.items[Item["id"]] = dict(Item)
        return {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames=None,
                    ExpressionAttributeValues=None):
        self.update_calls.append({
            "Key": Key, "UpdateExpression": UpdateExpression,
            "ExpressionAttributeNames": ExpressionAttributeNames,
            "ExpressionAttributeValues": ExpressionAttributeValues,
        })
        item = self.items.get(Key["id"])
        if item is None:
            return {}
        # 极简 SET 解析：把 "SET #a = :x, b = :y" 映射到实际字段。
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}
        body = UpdateExpression.strip()
        assert body.upper().startswith("SET ")
        for assign in body[4:].split(","):
            lhs, rhs = (p.strip() for p in assign.split("="))
            field = names.get(lhs, lhs)      # #s -> status
            item[field] = values[rhs]
        return {}

    def scan(self, **kwargs):
        values = kwargs.get("ExpressionAttributeValues") or {}
        rows = list(self.items.values())
        if ":e" in values and ":p" in values:  # supersede 过滤
            rows = [r for r in rows
                    if r.get("requester_email") == values[":e"]
                    and r.get("status") == values[":p"]]
        return {"Items": [dict(r) for r in rows]}


# --- fake SES client ---------------------------------------------------------
class FakeSes:
    """捕获 send_email 调用；返回带 MessageId 的响应。"""

    def __init__(self):
        self.calls = []

    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        return {"MessageId": "fake-message-id"}


@pytest.fixture
def fake_ses(monkeypatch):
    ses = FakeSes()
    monkeypatch.setattr(tr.boto3, "client", lambda *a, **k: ses)
    return ses


@pytest.fixture
def make_client(monkeypatch):
    """返回一个工厂：给定预置 items，装配 FakeTable + settings override + TestClient。"""
    def _factory(items=None):
        table = FakeTable(items)
        monkeypatch.setattr(tr, "_table", lambda settings: table)
        app.dependency_overrides[get_settings] = _settings
        client = TestClient(app)
        return client, table
    yield _factory
    app.dependency_overrides.clear()


# =============================================================================
# 9.9  _normalize_tier —— 归一化与别名覆盖（R53, R60）
# =============================================================================
def test_normalize_tier_canonical_passthrough():
    assert tr._normalize_tier("Pro") == "Pro"
    assert tr._normalize_tier("ProPlus") == "ProPlus"
    assert tr._normalize_tier("ProMax") == "ProMax"
    assert tr._normalize_tier("Power") == "Power"


@pytest.mark.parametrize("raw,expected", [
    ("pro+", "ProPlus"),
    ("pro plus", "ProPlus"),
    ("PRO PLUS", "ProPlus"),
    ("pro_plus", "ProPlus"),
    ("promax", "ProMax"),
    ("pro max", "ProMax"),
    ("pro-max", "ProMax"),
    ("PRO_MAX", "ProMax"),
    ("Pro Max", "ProMax"),
    ("pro", "Pro"),
    ("PRO", "Pro"),
    ("power", "Power"),
    ("  Power  ", "Power"),   # 前后空白
])
def test_normalize_tier_aliases(raw, expected):
    assert tr._normalize_tier(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "gold", "pro maxx", "enterprise", "pr o"])
def test_normalize_tier_illegal_returns_none(raw):
    assert tr._normalize_tier(raw) is None


def test_normalize_tier_none_returns_none():
    assert tr._normalize_tier(None) is None


# =============================================================================
# 9.10  POST /api/tier-requests —— API key 接受/拒绝 + 校验 400（R58, R59, R60）
# =============================================================================
def test_submit_missing_api_key_401(make_client):
    client, _ = make_client()
    r = client.post("/api/tier-requests",
                    json={"requester_email": "u@example.com", "target_tier": "Pro"})
    assert r.status_code == 401


def test_submit_wrong_api_key_401(make_client):
    client, _ = make_client()
    r = client.post("/api/tier-requests",
                    headers={"X-Api-Key": "wrong"},
                    json={"requester_email": "u@example.com", "target_tier": "Pro"})
    assert r.status_code == 401


def test_submit_correct_api_key_ok(make_client):
    client, table = make_client()
    r = client.post("/api/tier-requests",
                    headers={"X-Api-Key": API_KEY},
                    json={"requester_email": "u@example.com", "target_tier": "pro+"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["status"] == "pending"
    # 归一化后落库为 ProPlus
    assert len(table.put_calls) == 1
    assert table.put_calls[0]["target_tier"] == "ProPlus"


def test_submit_invalid_email_400(make_client):
    client, _ = make_client()
    r = client.post("/api/tier-requests",
                    headers={"X-Api-Key": API_KEY},
                    json={"requester_email": "not-an-email", "target_tier": "Pro"})
    assert r.status_code == 400


def test_submit_invalid_tier_400(make_client):
    client, _ = make_client()
    r = client.post("/api/tier-requests",
                    headers={"X-Api-Key": API_KEY},
                    json={"requester_email": "u@example.com", "target_tier": "gold"})
    assert r.status_code == 400


# =============================================================================
# 9.11  supersede —— 同一 email 连续提交，至多一条 pending（R61, R62, R63）
# =============================================================================
def test_submit_supersedes_existing_pending(make_client):
    existing = [{
        "id": "old-1", "requester_email": "u@example.com", "target_tier": "Pro",
        "status": "pending", "created_at": "2026-09-01T00:00:00+00:00",
    }]
    client, table = make_client(existing)
    r = client.post("/api/tier-requests",
                    headers={"X-Api-Key": API_KEY},
                    json={"requester_email": "u@example.com", "target_tier": "ProMax"})
    assert r.status_code == 200
    body = r.json()
    assert body["superseded"] >= 1

    # 旧记录状态翻转为 superseded。
    assert table.items["old-1"]["status"] == "superseded"

    # 该 email 至多一条 pending（即新提交的那条）。
    pending = [it for it in table.items.values()
               if it["requester_email"] == "u@example.com" and it["status"] == "pending"]
    assert len(pending) == 1
    assert pending[0]["target_tier"] == "ProMax"
    assert pending[0]["id"] == body["id"]


def test_submit_no_existing_pending_superseded_zero(make_client):
    client, table = make_client()
    r = client.post("/api/tier-requests",
                    headers={"X-Api-Key": API_KEY},
                    json={"requester_email": "fresh@example.com", "target_tier": "Pro"})
    assert r.status_code == 200
    assert r.json()["superseded"] == 0
    pending = [it for it in table.items.values() if it["status"] == "pending"]
    assert len(pending) == 1


# =============================================================================
# 9.12  close —— 写字段 + 一封 SES；非法 outcome 400；不存在 id 404
# =============================================================================
@pytest.mark.parametrize("outcome", ["completed", "rejected"])
def test_close_writes_fields_and_sends_one_email(make_client, fake_ses, outcome):
    seed = [{
        "id": "req-1", "requester_email": "u@example.com", "target_tier": "ProMax",
        "status": "pending", "created_at": "2026-09-01T00:00:00+00:00",
    }]
    client, table = make_client(seed)
    r = client.post("/api/tier-requests/req-1/close",
                    json={"outcome": outcome, "admin_note": "已核对"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["status"] == outcome
    assert body["email"]["sent"] is True

    item = table.items["req-1"]
    assert item["status"] == outcome
    assert item["processed_at"]                 # 已写入
    assert item["processed_by"]                 # 已写入（actor，TestClient 无 JWT -> unknown）
    assert item["admin_note"] == "已核对"

    # 恰好一封 SES 邮件，发给申请人、发件人为 settings.ses_sender。
    assert len(fake_ses.calls) == 1
    call = fake_ses.calls[0]
    assert call["Source"] == SENDER
    assert call["Destination"]["ToAddresses"] == ["u@example.com"]


def test_close_without_admin_note_still_sends_one_email(make_client, fake_ses):
    seed = [{
        "id": "req-2", "requester_email": "v@example.com", "target_tier": "Pro",
        "status": "pending", "created_at": "2026-09-01T00:00:00+00:00",
    }]
    client, table = make_client(seed)
    r = client.post("/api/tier-requests/req-2/close", json={"outcome": "completed"})
    assert r.status_code == 200
    item = table.items["req-2"]
    assert item["status"] == "completed"
    assert "admin_note" not in item             # 未提供则不写
    assert len(fake_ses.calls) == 1


def test_close_invalid_outcome_400(make_client, fake_ses):
    seed = [{
        "id": "req-3", "requester_email": "u@example.com", "target_tier": "Pro",
        "status": "pending", "created_at": "2026-09-01T00:00:00+00:00",
    }]
    client, table = make_client(seed)
    r = client.post("/api/tier-requests/req-3/close", json={"outcome": "maybe"})
    assert r.status_code == 400
    assert len(fake_ses.calls) == 0             # 未发邮件
    assert table.items["req-3"]["status"] == "pending"  # 未改动


def test_close_nonexistent_id_404(make_client, fake_ses):
    client, _ = make_client()                   # 空表 -> get_item 无 Item
    r = client.post("/api/tier-requests/nope/close", json={"outcome": "completed"})
    assert r.status_code == 404
    assert len(fake_ses.calls) == 0


# =============================================================================
# 9.13  GET /api/tier-requests —— 排序：pending 优先、再 created_at 倒序（R66, R74）
# =============================================================================
def test_list_sorted_pending_first_then_created_desc(make_client):
    seed = [
        {"id": "a", "requester_email": "a@example.com", "target_tier": "Pro",
         "status": "completed", "created_at": "2026-09-05T00:00:00+00:00"},
        {"id": "b", "requester_email": "b@example.com", "target_tier": "Pro",
         "status": "pending", "created_at": "2026-09-02T00:00:00+00:00"},
        {"id": "c", "requester_email": "c@example.com", "target_tier": "Pro",
         "status": "pending", "created_at": "2026-09-04T00:00:00+00:00"},
        {"id": "d", "requester_email": "d@example.com", "target_tier": "Pro",
         "status": "superseded", "created_at": "2026-09-03T00:00:00+00:00"},
        {"id": "e", "requester_email": "e@example.com", "target_tier": "Pro",
         "status": "rejected", "created_at": "2026-09-06T00:00:00+00:00"},
    ]
    client, _ = make_client(seed)
    r = client.get("/api/tier-requests")
    assert r.status_code == 200
    ids = [it["id"] for it in r.json()["items"]]
    # pending 组（c=09-04, b=09-02，组内倒序）在前；
    # 非 pending 组（e=09-06, a=09-05, d=09-03，倒序）在后。
    assert ids == ["c", "b", "e", "a", "d"]
