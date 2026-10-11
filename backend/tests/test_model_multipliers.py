"""模型倍率维护（model-multipliers）后端单测：不触碰 AWS。

镜像 test_tier_requests.py / test_api.py 的 fake 层风格：
- FakeTable 顶替 DynamoDB Table（scan/put_item），主键为 `model`；
- FakeLayer 顶替 QueryLayer，`run(model_list())` 返回预置的 Athena distinct model；
- get_settings 用 app.dependency_overrides 注入非空表名；get_query_layer 注入 FakeLayer；
- TestClient(app) 无 aws.event，main.py 中间件跳过组校验，管理端可达。

覆盖 FEAT-001 验收：GET 空表写种子且 key 为真实 model 字符串；GET 把 Athena 有/表
没有的 model 以 source=unknown 补齐；PUT 更新 multiplier 落 source=edited；非法
（负数/非数值）multiplier 返回 400。
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api import model_multipliers as mm
from backend.api.deps import get_query_layer
from backend.api.settings import Settings, get_settings
from backend.shared.athena_client import QuerySpec


# 探针确认的真实 Athena distinct model 集合（倍率表 key 的权威来源）。
ATHENA_MODELS = ["auto", "claude_opus_5", "claude_sonnet_5", "gpt_5.6_sol", "gpt_5.6_terra"]


def _settings() -> Settings:
    """一组测试用配置：倍率表名非空。"""
    return Settings(model_multiplier_table="ModelMultipliers-test")


# --- fake DynamoDB Table -----------------------------------------------------
class FakeTable:
    """内存版 DynamoDB Table，主键为 `model`。支持 scan/put_item。"""

    def __init__(self, items=None):
        self.items = {it["model"]: dict(it) for it in (items or [])}
        self.put_calls = []

    def scan(self, **kwargs):
        return {"Items": [dict(v) for v in self.items.values()]}

    def put_item(self, Item):
        self.put_calls.append(dict(Item))
        self.items[Item["model"]] = dict(Item)
        return {}


# --- fake QueryLayer ---------------------------------------------------------
class FakeLayer:
    """只需支持 run(model_list())，返回预置的 Athena distinct model 行。"""

    def __init__(self, models):
        self._models = models

    def run(self, spec: QuerySpec):
        return [{"model": m} for m in self._models]


@pytest.fixture
def make_client(monkeypatch):
    """工厂：给定预置 items 与 athena models，装配 FakeTable + overrides + TestClient。"""
    def _factory(items=None, athena_models=None):
        table = FakeTable(items)
        monkeypatch.setattr(mm, "_table", lambda settings: table)
        app.dependency_overrides[get_settings] = _settings
        app.dependency_overrides[get_query_layer] = lambda: FakeLayer(
            ATHENA_MODELS if athena_models is None else athena_models)
        client = TestClient(app)
        return client, table
    yield _factory
    app.dependency_overrides.clear()


# =============================================================================
# GET —— 空表写种子，key 为真实 model 字符串
# =============================================================================
def test_get_empty_table_writes_full_seed(make_client):
    """空表时写入**全部** SEED（21 个官方倍率），不按 Athena 命中过滤。"""
    client, table = make_client()
    r = client.get("/api/model-multipliers")
    assert r.status_code == 200
    items = {it["model"]: it for it in r.json()["items"]}

    # 全部 SEED 条目都已写入表（预置完整倍率表）。
    assert set(table.items) == set(mm.SEED)
    assert len(table.items) == 21
    # 全部为 official（倍率来自 Kiro IDE 模型选择器实测截图）。
    assert all(v["source"] == "official" for v in mm.SEED.values())
    # 抽样校验若干关键值 + 真实 model 字符串 key。
    assert items["auto"] == {"model": "auto", "multiplier": 1.0, "source": "official"}
    assert items["gpt_5.6_sol"]["multiplier"] == 4.4
    assert items["gpt_5.6_terra"]["multiplier"] == 2.2
    assert items["claude_opus_5"]["multiplier"] == 2.2
    assert items["claude_opus_5.5"]["multiplier"] == 2.0   # 点号保留
    assert items["claude_sonnet_5"] == {"model": "claude_sonnet_5",
                                        "multiplier": 1.3, "source": "official"}
    assert items["qwen3_coder_next"]["multiplier"] == 0.05


# =============================================================================
# GET —— Athena 有、表没有的 model 以 source=unknown 补齐（不写入表）
# =============================================================================
def test_get_fills_unknown_for_athena_only_models(make_client):
    # 已有一条编辑过的 auto；Athena 多出一个种子没有的 model。
    seed = [{"model": "auto", "multiplier": Decimal("1.5"), "source": "edited"}]
    extra_models = ATHENA_MODELS + ["mystery_model_x"]
    client, table = make_client(items=seed, athena_models=extra_models)

    r = client.get("/api/model-multipliers")
    assert r.status_code == 200
    items = {it["model"]: it for it in r.json()["items"]}

    # 表非空 -> 不触发种子写入；已有 auto 保留编辑值。
    assert items["auto"] == {"model": "auto", "multiplier": 1.5, "source": "edited"}
    # Athena 有、表没有的 model 以 unknown/null 补齐。
    assert items["mystery_model_x"] == {"model": "mystery_model_x",
                                        "multiplier": None, "source": "unknown"}
    # 其余 Athena model（种子没落库，因为表非空跳过写种子）也以 unknown 补齐。
    assert items["gpt_5.6_sol"] == {"model": "gpt_5.6_sol",
                                    "multiplier": None, "source": "unknown"}
    # 未写入表（补齐仅在返回层）。
    assert "mystery_model_x" not in table.items


# =============================================================================
# PUT 批量 —— 更新 multiplier 落 source=edited
# =============================================================================
def test_put_batch_updates_multiplier_source_edited(make_client):
    client, table = make_client()
    r = client.put("/api/model-multipliers",
                   json={"items": [{"model": "auto", "multiplier": 1.25},
                                   {"model": "gpt_5.6_sol", "multiplier": 5}]})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "updated": 2}
    assert table.items["auto"]["source"] == "edited"
    assert float(table.items["auto"]["multiplier"]) == 1.25
    assert table.items["gpt_5.6_sol"]["source"] == "edited"
    assert float(table.items["gpt_5.6_sol"]["multiplier"]) == 5.0


# =============================================================================
# PUT 单个 —— 更新 multiplier 落 source=edited
# =============================================================================
def test_put_single_updates_multiplier_source_edited(make_client):
    client, table = make_client()
    r = client.put("/api/model-multipliers/claude_opus_5", json={"multiplier": 3.0})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["source"] == "edited"
    assert table.items["claude_opus_5"]["source"] == "edited"
    assert float(table.items["claude_opus_5"]["multiplier"]) == 3.0


# =============================================================================
# PUT —— 非法 multiplier 返回 400（负数 / 非数值），不落库
# =============================================================================
def test_put_batch_negative_multiplier_400(make_client):
    client, table = make_client()
    r = client.put("/api/model-multipliers",
                   json={"items": [{"model": "auto", "multiplier": -1}]})
    assert r.status_code == 400
    assert "auto" not in table.put_calls and not table.put_calls


def test_put_batch_non_numeric_multiplier_400(make_client):
    client, table = make_client()
    r = client.put("/api/model-multipliers",
                   json={"items": [{"model": "auto", "multiplier": "abc"}]})
    assert r.status_code == 400
    assert not table.put_calls


def test_put_single_negative_multiplier_400(make_client):
    client, table = make_client()
    r = client.put("/api/model-multipliers/auto", json={"multiplier": -0.5})
    assert r.status_code == 400
    assert not table.put_calls


def test_put_batch_empty_items_400(make_client):
    client, _ = make_client()
    r = client.put("/api/model-multipliers", json={"items": []})
    assert r.status_code == 400
