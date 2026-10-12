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
    from backend.shared.recommendations import annotate_tier_steps as _annotate_tier_steps
    from backend.api.settings import Settings, get_settings
    from backend.api.deps import (
        get_queries, get_query_layer, get_identity_filter,
    )
except ImportError:  # Lambda 平铺打包
    from shared.athena_client import Queries, QueryLayer  # type: ignore
    from shared.recommendations import recommendations as _recommendations  # type: ignore
    from shared.recommendations import annotate_tier_steps as _annotate_tier_steps  # type: ignore
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

# 模型倍率维护路由（/api/model-multipliers）；组校验由下方中间件统一处理
try:
    from backend.api.model_multipliers import router as model_multipliers_router
except ImportError:  # Lambda 平铺打包
    from model_multipliers import router as model_multipliers_router  # type: ignore
app.include_router(model_multipliers_router)

# CORS：具体 origin 由 CloudFront 域注入；开发放宽
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    # 让浏览器能读到 CSV 导出的文件名（跨域下非安全列表头默认不暴露）。
    expose_headers=["Content-Disposition"],
)

REQUIRED_GROUP = os.environ.get("REQUIRED_GROUP", "admins")
# "/api/tier-requests" 放行的是**公开提交** POST（网关侧为匿名路由，靠共享
# X-Api-Key 保护，不挂 JWT authorizer）。GET /api/tier-requests（列表）与 POST
# /api/tier-requests/{id}/close 共/异于此路径：GET 虽同路径，但网关侧走
# /api/{proxy+} 的 JWT 授权路由（精确匿名路由仅匹配 POST，见任务 9.17），故 GET
# 仍受网关 JWT 保护；close 是不同路径、未列入本集合，应用层组校验照常生效。
_PUBLIC_PATHS = {"/api/health", "/docs", "/openapi.json", "/redoc", "/api/tier-requests"}

# 成本说明（方案 A）：订阅月费（按档位整月计）+ 超额成本，非账单。
_EST_COST_NOTE = (
    "估算：订阅月费（按档位，Pro $20 / Pro+ $40 / Pro Max $100 / Power $200，整月计）"
    " + 超额成本（超出额度部分 $0.04/credit）；月费未按在订天数折算，"
    "量级与订阅账单一致，精确对账请以 AWS 账单 / Cost Explorer 为准。"
)
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
    # D3 口径：注册数 = 名单 ∪ 有用量用户（含名单外的真实用量用户）。
    registered = _registered_total(settings, _usage_emails(q, layer, date_from, date_to, idf)) or total
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
        "est_cost_note": _EST_COST_NOTE,
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
    # D3 口径：注册数 = 名单 ∪ 有用量用户。
    registered = _registered_total(settings, _usage_emails(q, layer, date_from, date_to, idf)) or total
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
    upgrades, downgrades = _recommendations(users)       # 保留（兼容 + 不破 test_report）
    users_annotated = _annotate_tier_steps(users)        # 使用率阈值模型：全量名单 + 升/降一档建议
    return {
        "range": {"from": date_from, "to": date_to},
        "kpis": kpis,
        "upgrades": upgrades,
        "downgrades": downgrades,
        "users": users_annotated,
        "est_cost_note": _EST_COST_NOTE,
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


@app.get("/api/model-concentration")
def model_concentration(
    models: str = Query(..., description="逗号分隔的模型名"),
    threshold: float = Query(0.5, ge=0.0, le=1.0),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    period: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
    bins: int = Query(10, ge=2, le=50),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    idf: Optional[dict] = Depends(get_identity_filter),
    settings: Settings = Depends(get_settings),
):
    """模型集中度分析（模块 A，口径见只读调研报告）。

    统计口径=消息数占比（非成本占比）；分母=区间内有消息的员工（已排除 0 用量用户）。
    单条明细查询取每员工 ratio，在 Python 里算汇总 + 等宽直方图分桶，并回传 ratios
    供前端在阈值滑块变化时本地重算（无需重新请求）。
    """
    date_from, date_to = _resolve_range(period, date_from, date_to)
    # 1) 模型白名单校验（用 model_list 的 distinct model 结果）
    valid = {r["model"] for r in layer.run(q.model_list())}
    requested = [m.strip() for m in models.split(",") if m.strip()]
    chosen = [m for m in requested if m in valid]
    if not chosen:
        return JSONResponse(status_code=400, content={
            "detail": "no valid model selected", "valid_models": sorted(valid)})
    # 2) 单条明细查询：每员工 ratio（+ target/total 消息数）
    rows = layer.run(q.model_concentration_detail(
        chosen, date_from, date_to, identity_filter=idf))
    # 姓名/部门关联：复用 5 分钟缓存的注册名单（DEPT_TABLE 未配/扫描失败→空表→全部走回退）。
    roster_by_email = {u["email"]: u for u in _roster(settings)}
    members = []
    for r in rows:
        email = (r.get("email") or "").strip().lower()
        person = roster_by_email.get(email) or {}
        name = (person.get("display_name") or "").strip() or email.split("@")[0]
        dept = (person.get("department") or "").strip() or "\u672a\u5206\u7ec4"
        members.append({
            "email": email,
            "display_name": name,
            "department": dept,
            # 查询层键 target_msgs/total_msgs → 对外键 target_messages/total_messages（勿透传）
            "target_messages": int(r.get("target_msgs") or 0),
            "total_messages": int(r.get("total_msgs") or 0),
            "ratio": float(r.get("ratio") or 0.0),
        })
    ratios = [m["ratio"] for m in members]
    total = len(ratios)
    concentrated = sum(1 for x in ratios if x >= threshold)
    # 3) 等宽直方图分桶 [0,1]；x==1.0 落到最后一桶
    hist = [0] * bins
    for x in ratios:
        idx = min(bins - 1, int(x * bins))
        hist[idx] += 1
    buckets = [{"from": round(i / bins, 4), "to": round((i + 1) / bins, 4),
                "count": hist[i]} for i in range(bins)]
    return {
        "range": {"from": date_from, "to": date_to},
        "models": chosen,
        "threshold": threshold,
        "total_users": total,
        "concentrated_users": concentrated,
        "concentration_rate": round(concentrated / total, 4) if total else 0.0,
        "histogram": buckets,
        "ratios": [round(x, 4) for x in ratios],
        "members": members,
    }


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
    include_all: bool = Query(True),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    settings: Settings = Depends(get_settings),
    idf: Optional[dict] = Depends(get_identity_filter),
):
    # include_all=True（默认）：以全量注册名单为基准，零用量补 0，内存排序+分页。
    if include_all:
        merged = _users_merged(settings, q, layer, date_from, date_to,
                               department, search, idf)
        total = len(merged)
        ordered = _sort_users(merged, sort, direction)
        start = (page - 1) * page_size
        items = ordered[start:start + page_size]
        return {
            "total": total, "page": page, "page_size": page_size,
            "sort": sort, "direction": direction, "items": items,
        }

    # include_all=False：旧行为，仅区间内有用量的用户，由 Athena 分页。
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
        "status": _status(
            r.get("usage_rate") or 0, bool(r.get("is_overage")),
            has_usage=(float(r.get("credits_used") or 0) > 0
                       or int(r.get("total_messages") or 0) > 0)),
    } for r in rows]
    return {
        "total": total, "page": page, "page_size": page_size,
        "sort": sort, "direction": direction, "items": items,
    }


@app.get("/api/users/export")
def users_export(
    department: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    sort: str = Query("credits_used"),
    direction: str = Query("desc"),
    q: Queries = Depends(get_queries),
    layer: QueryLayer = Depends(get_query_layer),
    settings: Settings = Depends(get_settings),
    idf: Optional[dict] = Depends(get_identity_filter),
):
    """导出当前部门 tab + 搜索范围下的全量用户为 CSV（含零用量，不分页）。"""
    import csv
    import io
    import urllib.parse
    from datetime import datetime

    from fastapi.responses import Response

    merged = _users_merged(settings, q, layer, date_from, date_to,
                           department, search, idf)
    ordered = _sort_users(merged, sort, direction)

    status_cn = {"overage": "超额", "near_limit": "接近上限",
                 "normal": "正常", "unused": "未使用"}
    headers = ["邮箱", "姓名", "部门", "档位", "Credits", "额度", "余额",
               "使用率", "是否超额", "预估成本(USD)", "总消耗", "活跃天数", "状态"]
    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8 BOM，确保 Excel 正确识别中文
    w = csv.writer(buf)
    w.writerow(headers)
    for r in ordered:
        w.writerow([
            r.get("email") or "",
            r.get("display_name") or "",
            r.get("department") or "",
            r.get("subscription_tier") or "-",
            r.get("credits_used") or 0,
            r.get("usage_limit") or 0,
            r.get("balance") or 0,
            r.get("usage_rate") or 0,
            "是" if r.get("is_overage") else "否",
            r.get("est_cost") or 0,
            int(r.get("total_messages") or 0),
            int(r.get("active_days") or 0),
            status_cn.get(r.get("status") or "", r.get("status") or ""),
        ])

    dept_part = (department or "全部").replace("/", "_").replace("\\", "_")
    # 时间段：有起止日期则用 {from}_{to}（去掉连字符），否则回退到导出日期。
    def _compact(d: str) -> str:
        return (d or "").replace("-", "")
    if date_from and date_to:
        range_part = f"{_compact(date_from)}-{_compact(date_to)}"
    elif date_from:
        range_part = f"{_compact(date_from)}-至今"
    else:
        range_part = f"截至{datetime.now().strftime('%Y%m%d')}"
    fname = f"kiro-users-{dept_part}-{range_part}.csv"
    quoted = urllib.parse.quote(fname)
    # HTTP 头只能是 latin-1：filename= 用 ASCII 回退名（不含部门中文，但保留时间段），
    # 真实（含中文部门）名走 filename*。
    ascii_range = range_part if range_part.isascii() else datetime.now().strftime('%Y%m%d')
    ascii_fallback = f"kiro-users-{ascii_range}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"
            ),
        },
    )


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


def _status(usage_rate: float, is_overage: bool = False, has_usage: bool = False) -> str:
    """返回与语言无关的状态 key，由前端本地化为中/英文。

    `has_usage`：是否真实用过（credits_used>0 或 total_messages>0）。用于区分
    「极少量用量」与「完全未使用」：usage_rate 经 round(,4) 后，极小用量（如 Pro 档
    消耗 0.04 credit → rate≈0.00004）会被归零，若仅看 usage_rate 会误判为「未使用」。
    只要真实用过（哪怕 1 条消息），就应归「正常」而非「未使用」。
    """
    if is_overage or usage_rate >= 1.0:
        return "overage"
    if usage_rate >= 0.8:
        return "near_limit"
    if usage_rate > 0 or has_usage:
        return "normal"
    return "unused"


_REGISTERED_CACHE: dict[str, tuple[float, int]] = {}
_ROSTER_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _roster(settings: Settings) -> list[dict]:
    """全量注册名单（user_department 表全扫），带 5 分钟内存缓存。

    返回 [{email, display_name, department}]。表未配置/扫描失败返回空列表。
    注：名单来自 identity-sync（订阅席位），含 email/部门/姓名，但**不含**档位/额度
    （档位只在用量 CSV）。
    """
    import time as _time
    table = settings.dept_table
    if not table:
        return []
    hit = _ROSTER_CACHE.get(table)
    now = _time.time()
    if hit and (now - hit[0]) < 300:
        return hit[1]
    out: list[dict] = []
    try:
        import boto3
        ddb = boto3.client("dynamodb", region_name=settings.region)
        kw = {"TableName": table,
              "ProjectionExpression": "user_key, display_name, department"}
        while True:
            resp = ddb.scan(**kw)
            for it in resp.get("Items", []):
                out.append({
                    "email": (it.get("user_key", {}).get("S") or "").strip().lower(),
                    "display_name": it.get("display_name", {}).get("S") or "",
                    "department": it.get("department", {}).get("S") or "\u672a\u5206\u7ec4",
                })
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kw["ExclusiveStartKey"] = lek
    except Exception:  # noqa: BLE001
        return []
    out = [u for u in out if u["email"]]
    _ROSTER_CACHE[table] = (now, out)
    return out


def _users_merged(settings, q, layer, date_from, date_to, department, search, idf) -> list[dict]:
    """以注册名单为基准，LEFT JOIN 区间用量，零用量补 0。

    - 名单：_roster()（DynamoDB 全量）；
    - 用量：Athena all_users_usage()（按 email 聚合）；
    - 部门过滤以**名单部门**为准（tab 语义）；search 按姓名/邮箱模糊。
    - 零用量用户：credits=0 等，档位/额度未知（数据源所限）。
    返回前端行列表（未排序/未分页）。
    """
    roster = _roster(settings)
    usage_rows = layer.run(q.all_users_usage(date_from, date_to, identity_filter=idf))
    usage_by_email = {(r.get("email") or "").strip().lower(): r for r in usage_rows}

    # 注册名单为空（未配 DEPT_TABLE）时，降级为仅用量用户（旧行为）。
    if not roster:
        base = [{"email": e, "display_name": u.get("display_name") or "",
                 "department": u.get("department") or "\u672a\u5206\u7ec4"}
                for e, u in usage_by_email.items()]
    else:
        # 注册名单 ∪ 有用量用户（D3 口径）：有些用户在 Kiro 用量数据(Athena)里有记录、
        # 却不在 Identity Center 订阅应用分配名单里（未分配/直接在 Kiro 开席位/IdC 无此用户）。
        # 这些人真实消耗了 credits，应纳入明细/CSV，否则档位计数与 summary(Athena 口径)对不上。
        # 名单外用量用户部门归“未分组”（identity-sync 无其部门），档位/额度用 Athena 真实值。
        base = list(roster)
        roster_emails = {p["email"] for p in roster}
        for e, u in usage_by_email.items():
            if e not in roster_emails:
                base.append({
                    "email": e,
                    "display_name": u.get("display_name") or e.split("@")[0],
                    "department": "\u672a\u5206\u7ec4",
                })

    s = (search or "").strip().lower()
    out: list[dict] = []
    for person in base:
        email = person["email"]
        dept = person.get("department") or "\u672a\u5206\u7ec4"
        name = person.get("display_name") or ""
        # 部门 tab 过滤（以名单部门为准）
        if department and dept != department:
            continue
        if s and (s not in email.lower()) and (s not in name.lower()):
            continue
        u = usage_by_email.get(email)
        if u:
            rate = float(u.get("usage_rate") or 0)
            is_over = bool(u.get("is_overage"))
            out.append({
                "email": email,
                "display_name": name or (u.get("display_name") or ""),
                "department": dept,
                "subscription_tier": u.get("subscription_tier") or "",
                "credits_used": u.get("credits_used") or 0,
                "usage_limit": u.get("usage_limit") or 0,
                "balance": u.get("balance") or 0,
                "usage_rate": rate,
                "is_overage": is_over,
                "est_cost": u.get("est_cost") or 0,
                "total_messages": int(u.get("total_messages") or 0),
                "active_days": int(u.get("active_days") or 0),
                "status": _status(
                    rate, is_over,
                    has_usage=(float(u.get("credits_used") or 0) > 0
                               or int(u.get("total_messages") or 0) > 0)),
            })
        else:
            # 零用量注册用户：用量全 0，档位/额度未知
            out.append({
                "email": email, "display_name": name, "department": dept,
                "subscription_tier": "", "credits_used": 0, "usage_limit": 0,
                "balance": 0, "usage_rate": 0, "is_overage": False, "est_cost": 0,
                "total_messages": 0, "active_days": 0, "status": "unused",
            })
    return out


def _sort_users(rows: list[dict], sort: str, direction: str) -> list[dict]:
    """在内存按指定列排序（白名单列；非法列回退 credits_used desc）。"""
    allowed = {"credits_used", "usage_limit", "balance", "usage_rate",
               "total_messages", "active_days", "est_cost", "email"}
    col = sort if sort in allowed else "credits_used"
    rev = (direction or "desc").lower() != "asc"
    return sorted(rows, key=lambda r: (r.get(col) if r.get(col) is not None else ("" if col == "email" else 0)), reverse=rev)


def _usage_emails(q, layer, date_from, date_to, idf) -> set:
    """本次区间 Athena 有用量记录的 email 集合（小写）。供注册数并集口径（D3）用。

    复用 all_users_usage（QueryLayer 带 TTL 缓存，与 _users_merged 命中同一结果）。
    """
    try:
        rows = layer.run(q.all_users_usage(date_from, date_to, identity_filter=idf))
        return {(r.get("email") or "").strip().lower() for r in rows if r.get("email")}
    except Exception:  # noqa: BLE001
        return set()


def _registered_total(settings: Settings, usage_emails: Optional[set] = None) -> int:
    """注册用户总数 = 注册名单(DynamoDB) ∪ 有用量用户(Athena) 的去重计数（D3 口径）。

    作为激活率分母。D3 之前只数部门表条目；现在并上「Athena 有用量但不在名单」的用户
    （与 _users_merged 的并集口径一致，使注册数 / 档位分布 / 明细表 / CSV 全站统一）。
    `usage_emails`：本次区间 Athena 有用量的 email 集合（小写）；None 时退化为仅名单计数。
    名单扫描结果带 5 分钟缓存（缓存的是名单 email 集合，并集每次按传入 usage_emails 现算）。
    未配置 DEPT_TABLE 或扫描失败时返回 0（调用方回退到区间总用户数）。
    """
    import time as _time

    table = settings.dept_table
    if not table:
        return 0  # 未配置名单表：回退（调用方用 `or total`）
    hit = _REGISTERED_CACHE.get(table)
    now = _time.time()
    roster_emails: Optional[set] = None
    if hit and (now - hit[0]) < 300:
        roster_emails = hit[1]
    if roster_emails is None:
        try:
            import boto3
            ddb = boto3.client("dynamodb", region_name=settings.region)
            roster_emails = set()
            kw = {"TableName": table, "ProjectionExpression": "user_key"}
            while True:
                resp = ddb.scan(**kw)
                for it in resp.get("Items", []):
                    e = (it.get("user_key", {}).get("S") or "").strip().lower()
                    if e:
                        roster_emails.add(e)
                lek = resp.get("LastEvaluatedKey")
                if not lek:
                    break
                kw["ExclusiveStartKey"] = lek
        except Exception:  # noqa: BLE001
            return 0  # 名单扫描失败：回退（调用方用 `or total`）
        _REGISTERED_CACHE[table] = (now, roster_emails)
    return len(roster_emails | (usage_emails or set()))
