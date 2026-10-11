"""模型倍率（credit multiplier）维护接口与存储访问。

每个模型消耗 credit 时按「倍率」折算；本模块提供倍率的读取（GET，含种子补齐与
未知模型补齐）与管理端编辑（PUT）。存储用 DynamoDB（boto3 **resource** 模式），
表名取自 `settings.model_multiplier_table`（由 env `MODEL_MULTIPLIER_TABLE` 注入），
主键为 `model`（与 Athena fact_user_model_daily.model 字符串对齐）。

倍率 `source` 语义：
- official：来自 kiro.dev 官方倍率表（2026-10-08）；
- estimated：无官方对应条目时的推测值（如 DB 的 claude_sonnet_5 对应官方 Sonnet 5.5）；
- edited：管理员在维护页手动编辑过；
- unknown：数据里出现、但种子未覆盖的模型（multiplier=null，不预选，仍计入分母）。

路由模式仿 tier_requests.py（boto3 resource 懒初始化 + _table(settings) + import-fallback），
组校验由 main.py 中间件统一处理（admins 组）。
"""
from __future__ import annotations

import logging
from numbers import Real
from typing import Optional

import boto3
from fastapi import APIRouter, Body, Depends, Path
from fastapi.responses import JSONResponse

try:
    from backend.api.settings import Settings, get_settings
    from backend.api.deps import get_queries, get_query_layer
    from backend.shared.athena_client import Queries, QueryLayer
except ImportError:  # Lambda 平铺打包
    from settings import Settings, get_settings  # type: ignore
    from deps import get_queries, get_query_layer  # type: ignore
    from shared.athena_client import Queries, QueryLayer  # type: ignore

log = logging.getLogger("model_multipliers")

router = APIRouter(tags=["model-multipliers"])

# 种子倍率表：key = Athena fact_user_model_daily.model 的**真实字符串**（非显示名），
# value = {multiplier, source}。官方值来自 kiro.dev 倍率表（2026-10-08）；
# claude_sonnet_5 无官方同名条目（官方为 Sonnet 5.5=1.3），标 estimated。
# 数据里出现但不在本表的 model，GET 时以 multiplier=null, source='unknown' 补齐。
# key = Athena fact_user_model_daily.model 的真实字符串。ETL 不改模型列名
# （只去 `_messages` 后缀），故 key = 源 CSV `<model>_messages` 列的 <model> 原样：
# 全小写、空格→下划线、版本号点号保留（依据生产已确认的 gpt_5.6_sol / claude_opus_5）。
# 下列倍率全部来自 Kiro IDE 模型选择器实测截图（2026-10，用户提供），标 official。
# 生产数据里尚未出现的模型，其真实列名拼写按上述规律推断；若将来实际出现且拼写不符，
# 维护页会额外显示一条 unknown 条目，由管理员在费率维护页手动合并。
SEED: dict[str, dict] = {
    "auto": {"multiplier": 1.0, "source": "official"},
    "claude_opus_5.5": {"multiplier": 2.0, "source": "official"},
    "claude_sonnet_5.5": {"multiplier": 1.3, "source": "official"},
    "claude_opus_5": {"multiplier": 2.2, "source": "official"},
    "claude_sonnet_5": {"multiplier": 1.3, "source": "official"},
    "claude_opus_4.8": {"multiplier": 2.2, "source": "official"},
    "gpt_5.6_sol": {"multiplier": 4.4, "source": "official"},
    "gpt_5.6_terra": {"multiplier": 2.2, "source": "official"},
    "gpt_5.6_luna": {"multiplier": 0.6, "source": "official"},
    "claude_opus_4.7": {"multiplier": 2.2, "source": "official"},
    "claude_opus_4.6": {"multiplier": 2.2, "source": "official"},
    "claude_sonnet_4.6": {"multiplier": 1.3, "source": "official"},
    "claude_opus_4.5": {"multiplier": 2.2, "source": "official"},
    "claude_sonnet_4.5": {"multiplier": 1.3, "source": "official"},
    "claude_sonnet_4": {"multiplier": 1.3, "source": "official"},
    "claude_haiku_4.5": {"multiplier": 0.4, "source": "official"},
    "deepseek_v3.2": {"multiplier": 0.25, "source": "official"},
    "minimax_m2.5": {"multiplier": 0.25, "source": "official"},
    "minimax_m2.1": {"multiplier": 0.15, "source": "official"},
    "glm_5": {"multiplier": 0.5, "source": "official"},
    "qwen3_coder_next": {"multiplier": 0.05, "source": "official"},
}

# 懒初始化的 DynamoDB resource（模块级缓存），首次取表时按 settings.region 创建。
_dynamodb = None


def _resource(region: str):
    """返回缓存的 DynamoDB service resource（首次调用时创建）。"""
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=region)
    return _dynamodb


def _table(settings: Settings):
    """取 ModelMultipliers 的 Table 句柄，供 scan/put_item/update 复用。

    表名来自 `settings.model_multiplier_table`（env MODEL_MULTIPLIER_TABLE）；region
    复用 `settings.region`。表名为空时抛出，避免用错误的默认名静默访问。
    """
    table_name = settings.model_multiplier_table
    if not table_name:
        raise RuntimeError("MODEL_MULTIPLIER_TABLE 未配置")
    return _resource(settings.region).Table(table_name)


def _coerce_multiplier(raw) -> Optional[float]:
    """把倍率入参校验为非负 float；非法（非数值/负数/布尔）返回 None。"""
    # bool 是 int 的子类，显式排除，避免 True/False 被当成 1/0。
    if isinstance(raw, bool) or not isinstance(raw, Real):
        return None
    val = float(raw)
    if val < 0:
        return None
    return val


def _athena_models(q: Queries, layer: QueryLayer) -> list[str]:
    """Athena distinct model 列表（倍率表 key 的权威来源）。"""
    rows = layer.run(q.model_list())
    return [r["model"] for r in rows if r.get("model")]


def _scan_table(table) -> dict[str, dict]:
    """全表扫描（翻页汇总），返回 model -> item 映射。"""
    out: dict[str, dict] = {}
    scan_kwargs: dict = {}
    while True:
        resp = table.scan(**scan_kwargs)
        for it in resp.get("Items", []):
            out[it["model"]] = it
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key
    return out


@router.get("/api/model-multipliers")
def list_multipliers(
    settings: Settings = Depends(get_settings),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
):
    """列出全部模型倍率（含种子首次写入 + 未知模型补齐）。

    ① scan 表取已有倍率；
    ② 表为空时首次写入种子（仅写 SEED 中 key 命中当前 Athena distinct model 的条目）；
    ③ 把 Athena 里有、表仍没有的 model 以 multiplier=null, source='unknown' 补齐返回；
    返回 `{"items": [{"model", "multiplier", "source"}]}`，按 model 升序。
    """
    table = _table(settings)
    existing = _scan_table(table)
    athena_models = _athena_models(q, layer)

    # 表为空：首次写入**全部** SEED（预置完整倍率表，不按 Athena 命中过滤——
    # 这样维护页一开始就展示所有已知模型的官方倍率，未来模型出现即已有倍率）。
    if not existing:
        for model, seed in SEED.items():
            table.put_item(Item={"model": model, "multiplier": _to_decimal(seed["multiplier"]),
                                 "source": seed["source"]})
            existing[model] = {"model": model, "multiplier": seed["multiplier"],
                               "source": seed["source"]}

    # 组装返回：表内条目 + Athena 有但表没有的 model 以 unknown 补齐。
    items: dict[str, dict] = {}
    for model, it in existing.items():
        items[model] = {
            "model": model,
            "multiplier": _mult_out(it.get("multiplier")),
            "source": it.get("source") or "unknown",
        }
    for model in athena_models:
        if model not in items:
            items[model] = {"model": model, "multiplier": None, "source": "unknown"}

    return {"items": sorted(items.values(), key=lambda x: x["model"])}


@router.put("/api/model-multipliers")
def update_multipliers(
    body: dict = Body(...),
    settings: Settings = Depends(get_settings),
):
    """批量更新倍率（body `{"items": [{"model", "multiplier"}]}`）。

    每条校验 multiplier 为非负数值（任一非法整体 400，不落库）；通过后 put_item
    写入 source='edited'。返回 `{"ok": True, "updated": n}`。
    """
    raw_items = (body or {}).get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return JSONResponse(status_code=400, content={"detail": "items 不能为空"})

    # 先全量校验，任一非法则整体 400（避免部分写入）。
    validated: list[tuple[str, float]] = []
    for it in raw_items:
        model = (it or {}).get("model")
        if not isinstance(model, str) or not model.strip():
            return JSONResponse(status_code=400, content={"detail": "model 非法"})
        mult = _coerce_multiplier((it or {}).get("multiplier"))
        if mult is None:
            return JSONResponse(status_code=400,
                                content={"detail": f"multiplier 必须为非负数值: {model}"})
        validated.append((model, mult))

    table = _table(settings)
    for model, mult in validated:
        table.put_item(Item={"model": model, "multiplier": _to_decimal(mult),
                             "source": "edited"})
    return {"ok": True, "updated": len(validated)}


@router.put("/api/model-multipliers/{model}")
def update_multiplier(
    model: str = Path(...),
    body: dict = Body(...),
    settings: Settings = Depends(get_settings),
):
    """更新单个模型倍率（body `{"multiplier"}`）；校验非负数值，source 落 'edited'。"""
    mult = _coerce_multiplier((body or {}).get("multiplier"))
    if mult is None:
        return JSONResponse(status_code=400,
                            content={"detail": "multiplier 必须为非负数值"})
    table = _table(settings)
    table.put_item(Item={"model": model, "multiplier": _to_decimal(mult),
                         "source": "edited"})
    return {"ok": True, "model": model, "multiplier": mult, "source": "edited"}


# --- helpers ---------------------------------------------------------------
def _to_decimal(val: float):
    """把 float 转为 DynamoDB 可接受的 Decimal（resource 层不接受原生 float）。

    经 str() 中转避免二进制浮点噪声（如 1.3 -> Decimal('1.3')）。
    """
    from decimal import Decimal
    return Decimal(str(val))


def _mult_out(val) -> Optional[float]:
    """把表里存的 multiplier（Decimal/None）转成 JSON 友好的 float/None。"""
    if val is None:
        return None
    return float(val)
