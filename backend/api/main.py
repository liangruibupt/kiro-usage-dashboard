"""看板 REST API（FastAPI on Lambda）。

前缀 `/api`，全部 endpoint 需管理员 JWT（由 API Gateway JWT authorizer 校验 admins 组）。
路由只做参数校验 + 调用共享查询层 + 组装前端友好的响应；派生口径在查询层/derivations 统一。

对应 design.md §4 endpoint 表与 requirements R10–R17。
"""
from __future__ import annotations

from typing import Optional

from fastapi import Body, Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

try:
    from backend.shared.athena_client import Queries, QueryLayer
    from backend.shared.recommendations import recommendations as _recommendations
    from backend.api.settings import Settings, get_settings
    from backend.api.deps import (
        get_queries, get_query_layer, get_identity_filter,
    )
except ImportError:  # Lambda 平铺打包
    from shared.athena_client import Queries, QueryLayer  # type: ignore
    from shared.recommendations import recommendations as _recommendations  # type: ignore
    from settings import Settings, get_settings  # type: ignore
    from deps import get_queries, get_query_layer, get_identity_filter  # type: ignore

import os

from fastapi import Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Kiro Usage Dashboard API", version="1.0.0")

# 远程 MCP 端点（无状态手写 Streamable HTTP，见 mcp_server.py）：POST /mcp。
# 普通 FastAPI 路由，无 lifespan / 无 session manager，完美适配 Lambda。
try:
    from backend.api.mcp_server import router as mcp_router
except ImportError:  # Lambda 平铺打包
    from mcp_server import router as mcp_router  # type: ignore
app.include_router(mcp_router)

# 管理员管理路由（/api/admin/*）；组校验由下方中间件统一处理
try:
    from backend.api.admin_users import router as admin_router
except ImportError:  # Lambda 平铺打包
    from admin_users import router as admin_router  # type: ignore
app.include_router(admin_router)

# 调档申请路由（/api/tier-requests、close）；组校验由下方中间件统一处理
try:
    from backend.api.tier_requests import router as tier_requests_router
except ImportError:  # Lambda 平铺打包
    from tier_requests import router as tier_requests_router  # type: ignore
app.include_router(tier_requests_router)

# CORS：具体 origin 由 CloudFront 域注入；开发放宽
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

REQUIRED_GROUP = os.environ.get("REQUIRED_GROUP", "admins")
# "/api/tier-requests" 放行的是**公开提交** POST（网关侧为匿名路由，靠共享
# X-Api-Key 保护，不挂 JWT authorizer）。GET /api/tier-requests（列表）与 POST
# /api/tier-requests/{id}/close 共/异于此路径：GET 虽同路径，但网关侧走
# /api/{proxy+} 的 JWT 授权路由（精确匿名路由仅匹配 POST，见任务 9.17），故 GET
# 仍受网关 JWT 保护；close 是不同路径、未列入本集合，应用层组校验照常生效。
_PUBLIC_PATHS = {"/api/health", "/docs", "/openapi.json", "/redoc", "/api/tier-requests"}
# 远程 MCP 端点 /mcp（及其尾斜杠/子路径）由共享 X-Api-Key 在工具层校验，不走 admins
# 组校验，故按前缀放行。
_PUBLIC_PREFIXES = ("/mcp",)


@app.middleware("http")
async def enforce_admin_group(request: Request, call_next):
    """组级授权（R2/R4）：HTTP API 的 JWT authorizer 已校验签名/iss/aud，
    但无法断言 cognito:groups，这里在应用层强制要求 admins 组。

    本地/测试无 authorizer 上下文时（scope 无 aws.event）跳过，便于单测；
    生产环境 event 一定带 requestContext（Mangum 注入）。
    """
    if (request.url.path in _PUBLIC_PATHS
            or request.url.path.startswith(_PUBLIC_PREFIXES)
            or request.method == "OPTIONS"):
        return await call_next(request)

    event = request.scope.get("aws.event")
    if event is None:
        # 无 API Gateway 上下文（本地/TestClient）——不做组校验
        return await call_next(request)

    claims = (((event.get("requestContext") or {}).get("authorizer") or {})
              .get("jwt") or {}).get("claims") or {}
    group_set = _parse_groups(claims.get("cognito:groups"))
    if REQUIRED_GROUP not in group_set:
        return JSONResponse(status_code=403,
                            content={"detail": f"forbidden: requires group '{REQUIRED_GROUP}'"})
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/summary")
def summary(
    period: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    idf: Optional[dict] = Depends(get_identity_filter),
    settings: Settings = Depends(get_settings),
):
    date_from, date_to = _resolve_range(period, date_from, date_to)
    row = layer.one(q.summary(date_from, date_to, identity_filter=idf))
    total = int(row.get("total_users") or 0)      # 区间内有用量记录的用户数
    active = int(row.get("active_users") or 0)    # 区间内 credits_used>0
    daily_active_avg = row.get("daily_active_avg") or 0
    daily_active_peak = int(row.get("daily_active_peak") or 0)
    registered = _registered_total(settings) or total
    return {
        "range": {"from": date_from, "to": date_to},
        "kpis": {
            "registered_users": registered,
            "total_users": total,
            "active_users": active,
            "activation_rate": round(active / registered, 4) if registered else 0.0,
            "daily_active": daily_active_avg,
            "daily_active_avg": daily_active_avg,
            "daily_active_peak": daily_active_peak,
            "total_credits": row.get("total_credits") or 0,
            "overage_users": int(row.get("overage_users") or 0),
            "est_cost": row.get("est_cost") or 0,
            "avg_credits_per_active": row.get("avg_credits_per_active") or 0,
        },
        "tiers": {
            "Pro": int(row.get("tier_pro") or 0),
            "ProPlus": int(row.get("tier_proplus") or 0),
            "ProMax": int(row.get("tier_promax") or 0),
            "Power": int(row.get("tier_power") or 0),
        },
        "est_cost_note": f"估算：{settings.credit_unit_price} $/credit（Kiro 官方超额单价）",
    }


@app.get("/api/report")
def report(
    period: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    idf: Optional[dict] = Depends(get_identity_filter),
    settings: Settings = Depends(get_settings),
):
    """月度报告：汇总 KPI + 升/降档建议。与邮件报告共用共享判定算法（R47）。"""
    date_from, date_to = _resolve_range(period, date_from, date_to)
    srow = layer.one(q.summary(date_from, date_to, identity_filter=idf))
    total = int(srow.get("total_users") or 0)
    active = int(srow.get("active_users") or 0)
    registered = _registered_total(settings) or total
    kpis = {
        "registered_users": registered,
        "active_users": active,
        "activation_rate": round(active / registered, 4) if registered else 0.0,
        "total_credits": srow.get("total_credits") or 0,
        "overage_users": int(srow.get("overage_users") or 0),
        "est_cost": srow.get("est_cost") or 0,
        "daily_active_avg": srow.get("daily_active_avg") or 0,
        "daily_active_peak": int(srow.get("daily_active_peak") or 0),
        "tiers": {
            "Pro": int(srow.get("tier_pro") or 0),
            "ProPlus": int(srow.get("tier_proplus") or 0),
            "ProMax": int(srow.get("tier_promax") or 0),
            "Power": int(srow.get("tier_power") or 0),
        },
    }
    users = layer.run(q.report_users(date_from, date_to, identity_filter=idf))
    upgrades, downgrades = _recommendations(users)
    return {
        "range": {"from": date_from, "to": date_to},
        "kpis": kpis,
        "upgrades": upgrades,
        "downgrades": downgrades,
        "est_cost_note": f"估算：{settings.credit_unit_price} $/credit（Kiro 官方超额单价）",
    }


@app.get("/api/trend")
def trend(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    idf: Optional[dict] = Depends(get_identity_filter),
):
    rows = layer.run(q.trend(date_from, date_to, identity_filter=idf))
    return {"series": [
        {"date": r["date"], "credits": r.get("credits") or 0,
         "active_users": int(r.get("active_users") or 0)}
        for r in rows
    ]}


@app.get("/api/models")
def models(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    email: Optional[str] = Query(None),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    idf: Optional[dict] = Depends(get_identity_filter),
):
    # 管理员全量（idf=None）时，允许用 email 参数下钻到单用户；
    # 开启身份强制（v2，idf 非空）时不允许覆盖，以免越权。
    if idf is None and email:
        idf = {"email": email}
    rows = layer.run(q.models(date_from, date_to, identity_filter=idf))
    return {"models": [
        {"model": r["model"], "messages": int(r.get("messages") or 0)} for r in rows
    ]}


@app.get("/api/departments")
def departments(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    idf: Optional[dict] = Depends(get_identity_filter),
):
    rows = layer.run(q.departments(date_from, date_to, identity_filter=idf))
    return {"departments": [
        {"department": r["department"], "users": int(r.get("users") or 0),
         "total_credits": r.get("total_credits") or 0,
         "avg_credits": r.get("avg_credits") or 0}
        for r in rows
    ]}


@app.get("/api/top-users")
def top_users(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    limit: Optional[int] = Query(None, ge=1, le=500),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    idf: Optional[dict] = Depends(get_identity_filter),
    settings: Settings = Depends(get_settings),
):
    n = limit or settings.top_n
    rows = layer.run(q.top_users(date_from, date_to, limit=n, identity_filter=idf))
    return {"users": [
        {"email": r["email"], "display_name": r.get("display_name") or "",
         "department": r.get("department") or "未分组",
         "subscription_tier": r.get("subscription_tier") or "",
         "credits_used": r.get("credits_used") or 0}
        for r in rows
    ]}


@app.get("/api/users")
def users(
    department: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    sort: str = Query("credits_used"),
    direction: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    idf: Optional[dict] = Depends(get_identity_filter),
):
    total = int(layer.one(q.users_count(
        date_from, date_to, department=department, identity_filter=idf,
        search=search)).get("total") or 0)
    rows = layer.run(q.users(
        date_from, date_to, department=department, sort=sort, direction=direction,
        page=page, page_size=page_size, identity_filter=idf, search=search))
    items = [{
        "email": r["email"],
        "display_name": r.get("display_name") or "",
        "department": r.get("department") or "未分组",
        "subscription_tier": r.get("subscription_tier") or "",
        "credits_used": r.get("credits_used") or 0,
        "usage_limit": r.get("usage_limit") or 0,
        "balance": r.get("balance") or 0,
        "usage_rate": r.get("usage_rate") or 0,
        "is_overage": bool(r.get("is_overage")),
        "est_cost": r.get("est_cost") or 0,
        "total_messages": int(r.get("total_messages") or 0),
        "active_days": int(r.get("active_days") or 0),
        "status": _status(r.get("usage_rate") or 0, bool(r.get("is_overage"))),
    } for r in rows]
    return {
        "total": total, "page": page, "page_size": page_size,
        "sort": sort, "direction": direction, "items": items,
    }


@app.get("/api/filters")
def filters(
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
):
    # 三个 SELECT DISTINCT 相互独立，并行执行以避免累加到 API Gateway 29s 超时。
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_periods = ex.submit(layer.run, q.periods())
        f_depts = ex.submit(layer.run, q.department_list())
        f_models = ex.submit(layer.run, q.model_list())
        periods = [r["period"] for r in f_periods.result()]
        depts = [r["department"] for r in f_depts.result()]
        mdls = [r["model"] for r in f_models.result()]
    return {"periods": periods, "departments": depts, "models": mdls}


@app.post("/api/chat")
def chat(
    request: Request,
    body: dict = Body(...),
    settings: Settings = Depends(get_settings),
):
    """NL 问答：将用户问题转发给 AgentCore Runtime（以调用方的 Cognito bearer token
    作为入站鉴权）。前端只访问本 API（已有 CORS + admins 组校验），避免直连
    AgentCore 端点的浏览器 CORS 问题，且 runtime ARN 不暴露给浏览器。
    """
    import json as _json
    import urllib.parse
    import urllib.request
    import uuid

    arn = settings.agent_runtime_arn
    if not arn:
        return JSONResponse(status_code=503, content={"detail": "NL 问答未配置（AGENT_RUNTIME_ARN 为空）"})

    prompt = (body or {}).get("prompt") or ""
    if not prompt.strip():
        return JSONResponse(status_code=400, content={"detail": "prompt 不能为空"})

    # 从入站请求取 bearer token（透传给 AgentCore 做入站 JWT 鉴权）。
    # 注意：API Gateway JWT authorizer 校验的是 id token(aud)，而 AgentCore 入站
    # customJWT 校验 access token(client_id)。前端在 header 用 id token（过 API GW），
    # 在 body.access_token 传 access token（用于 AgentCore）；缺失时回退用 header token。
    header_auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    body_access = (body or {}).get("access_token") or ""
    agent_token = body_access or (header_auth[7:] if header_auth.lower().startswith("bearer ") else "")
    if not agent_token:
        return JSONResponse(status_code=401, content={"detail": "缺少可用于 Agent 鉴权的 token"})

    enc = urllib.parse.quote(arn, safe="")
    url = (f"https://bedrock-agentcore.{settings.region}.amazonaws.com"
           f"/runtimes/{enc}/invocations?qualifier=DEFAULT")
    session_id = (body.get("session_id") or uuid.uuid4().hex)
    # AgentCore 要求 session id 长度 >= 33
    if len(session_id) < 33:
        session_id = (session_id + uuid.uuid4().hex)[:40]

    req = urllib.request.Request(
        url, data=_json.dumps({"prompt": prompt, "session_id": session_id}).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {agent_token}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = _json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=502, content={"detail": f"Agent 调用失败: {e}"})

    return {"reply": data.get("reply") or data.get("output") or "", "session_id": session_id}


# --- helpers ---------------------------------------------------------------
def _parse_groups(raw) -> set:
    """把 cognito:groups 归一化为集合。

    不同来源格式：
    - list（原始 JWT 解码后）：['admins', 'engineers']
    - HTTP API JWT authorizer 注入的字符串：'[admins engineers]' 或 'admins,engineers'
    """
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {str(g).strip() for g in raw if str(g).strip()}
    s = str(raw).strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return {g for g in s.replace(",", " ").split() if g}


def _resolve_range(period: Optional[str], date_from: Optional[str],
                   date_to: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """period=YYYY-MM 展开为该月首末日（简单口径：01 ~ 31，Athena 字符串比较安全）。"""
    if period and not (date_from or date_to):
        return f"{period}-01", f"{period}-31"
    return date_from, date_to


def _status(usage_rate: float, is_overage: bool = False) -> str:
    """返回与语言无关的状态 key，由前端本地化为中/英文。"""
    if is_overage or usage_rate >= 1.0:
        return "overage"
    if usage_rate >= 0.8:
        return "near_limit"
    if usage_rate > 0:
        return "normal"
    return "unused"


_REGISTERED_CACHE: dict[str, tuple[float, int]] = {}


def _registered_total(settings: Settings) -> int:
    """注册用户总数 = 部门表（identity-sync 写入）条目数。

    作为激活率分母（对齐参照看板：注册总数）。带 5 分钟内存缓存，
    未配置 DEPT_TABLE 或扫描失败时返回 0（调用方回退到区间总用户数）。
    """
    import time as _time

    table = settings.dept_table
    if not table:
        return 0
    hit = _REGISTERED_CACHE.get(table)
    now = _time.time()
    if hit and (now - hit[0]) < 300:
        return hit[1]
    try:
        import boto3
        ddb = boto3.client("dynamodb", region_name=settings.region)
        count = 0
        kw = {"TableName": table, "Select": "COUNT"}
        while True:
            resp = ddb.scan(**kw)
            count += int(resp.get("Count") or 0)
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kw["ExclusiveStartKey"] = lek
    except Exception:  # noqa: BLE001
        return 0
    _REGISTERED_CACHE[table] = (now, count)
    return count
