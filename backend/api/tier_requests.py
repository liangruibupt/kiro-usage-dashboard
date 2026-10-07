"""档位申请（tier-requests）接口与存储访问。

本模块承载「调档申请」的公开提交端点与管理端列表/关闭端点（路由在后续任务补齐）。
存储用 DynamoDB（boto3 **resource** 模式），表名取自 `settings.tier_requests_table`
（由 env `TIER_REQUESTS_TABLE` 注入）。

本文件目前只建立 DynamoDB 访问脚手架：
- 一个懒初始化的 DynamoDB resource（模块级缓存，region 取 `settings.region`）；
- `_table(settings)` helper 返回 `Table` 句柄，供后续 put_item/scan/update 复用。

对应 requirements R64、R65；design §13.4、§13.5。
"""
from __future__ import annotations

import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import boto3
from fastapi import APIRouter, Body, Depends, Header, Path, Request
from fastapi.responses import JSONResponse

try:
    from backend.api.settings import Settings, get_settings
except ImportError:  # Lambda 平铺打包
    from settings import Settings, get_settings  # type: ignore  # noqa: F401

# 复用 admin_users.py 的邮箱校验与 actor 口径（同 import-fallback 模式）：
# _valid_email(email) -> Optional[str]（小写归一化 + 正则）；
# _actor(request) -> str（JWT claims email 优先，退 sub，退 "unknown"）。
try:
    from backend.api.admin_users import _valid_email, _actor
except ImportError:  # Lambda 平铺打包
    from admin_users import _valid_email, _actor  # type: ignore  # noqa: F401

log = logging.getLogger("tier_requests")

# 无独立前缀：后续逐个声明完整路径（POST/GET /api/tier-requests、close），
# 在 main.py 用 app.include_router(router) 挂载（与 admin_router 同法）。
router = APIRouter(tags=["tier-requests"])

# 懒初始化的 DynamoDB resource（模块级缓存），首次取表时按 settings.region 创建。
_dynamodb = None


def _resource(region: str):
    """返回缓存的 DynamoDB service resource（首次调用时创建）。"""
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=region)
    return _dynamodb


def _table(settings: Settings):
    """取 Tier_Request_Table 的 Table 句柄，供 put_item/scan/update 复用。

    表名来自 `settings.tier_requests_table`（env TIER_REQUESTS_TABLE）；region
    复用 `settings.region`。表名为空时抛出，避免用错误的默认名静默访问。
    """
    table_name = settings.tier_requests_table
    if not table_name:
        raise RuntimeError("TIER_REQUESTS_TABLE 未配置")
    return _resource(settings.region).Table(table_name)


# 档位允许清单与别名归一化（与 MCP 工具 `request_tier_change` 同表；R53/R59/R60）。
# 原始键：Pro | ProPlus | ProMax | Power。别名忽略大小写与下划线（key 已 .lower()
# 且 "_"->空格）；覆盖 pro+ / pro plus / promax / pro max / pro-max 等常见拼写。
_TIER_ALIASES = {
    "pro": "Pro",
    "pro+": "ProPlus", "proplus": "ProPlus", "pro plus": "ProPlus",
    "promax": "ProMax", "pro max": "ProMax", "pro-max": "ProMax",
    "power": "Power",
}


def _normalize_tier(raw: str) -> Optional[str]:
    """把用户输入的档位归一化为原始键；非法返回 None。

    归一化：strip -> lower -> "_" 替换为空格，再查别名表；未命中时若 `raw`
    本身已是合法原始键则原样返回，否则 None。
    """
    key = (raw or "").strip().lower().replace("_", " ")
    return _TIER_ALIASES.get(key) or (raw if raw in {"Pro", "ProPlus", "ProMax", "Power"} else None)


def _now_iso() -> str:
    """当前 UTC 时间的 ISO8601 字符串（用于 created_at；口径同 report handler）。"""
    return datetime.now(timezone.utc).isoformat()


def _audit(actor: str, action: str, target: str, extra: Optional[dict] = None) -> None:
    """结构化审计日志到 CloudWatch（沿用 admin_users._audit 的 actor/action/target/extra
    结构；用独立的 `TIER_AUDIT` 标签以便与 `ADMIN_AUDIT` 区分检索）。design §13.2/§13.5。"""
    log.info("TIER_AUDIT %s", json.dumps(
        {"actor": actor, "action": action, "target": target, **(extra or {})},
        ensure_ascii=False))


class TierSubmitError(Exception):
    """提交校验失败（邮箱/档位非法）。`field` 便于调用方映射错误。"""
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def submit_core(settings: Settings, requester_email: str, target_tier: str,
                reason: Optional[str] = None) -> dict:
    """提交调档申请的核心逻辑（不做 API key 校验 / 不绑 HTTP）。

    供 HTTP 路由 `submit` 与 MCP 工具 `request_tier_change` 共用（同进程直接
    写 DynamoDB，不绕 HTTP）。步骤：
    ② `requester_email` 邮箱校验（非法 → TierSubmitError('requester_email')）；
    ③ `target_tier` 归一化（非法 → TierSubmitError('target_tier')）；
    ④ supersede：把该 email 仍 pending 的历史记录改为 superseded（newest wins）；
    ⑤ put_item 写入 status=pending 的新记录。
    返回 `{ok, id, status, superseded}`。
    """
    email = _valid_email(requester_email or "")
    if not email:
        raise TierSubmitError("requester_email", "邮箱格式无效")

    tier = _normalize_tier(target_tier or "")
    if not tier:
        raise TierSubmitError("target_tier", "target_tier 非法")

    table = _table(settings)

    # supersede：scan 出该 email 仍为 pending 的历史记录，逐条 update 为 superseded。
    # #s 别名规避 DynamoDB 保留字 status。
    superseded = 0
    scan_kwargs = {
        "FilterExpression": "requester_email = :e AND #s = :p",
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": {":e": email, ":p": "pending"},
    }
    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            table.update_item(
                Key={"id": item["id"]},
                UpdateExpression="SET #s = :sup",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":sup": "superseded"},
            )
            superseded += 1
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    # put_item：新的 pending 记录（newest wins）。
    new_id = uuid.uuid4().hex
    record = {
        "id": new_id,
        "requester_email": email,
        "target_tier": tier,
        "status": "pending",
        "created_at": _now_iso(),
    }
    if reason is not None:
        record["reason"] = reason
    table.put_item(Item=record)

    # 结构化审计：公开提交无 JWT actor，用 requester_email 作为 actor 与 target。
    _audit(email, "submit", email,
           {"target_tier": tier, "superseded": superseded, "id": new_id})
    return {"ok": True, "id": new_id, "status": "pending", "superseded": superseded}


@router.post("/api/tier-requests")
def submit(
    request: Request,
    body: dict = Body(...),
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
    settings: Settings = Depends(get_settings),
):
    """公开提交调档申请（R55–R63；design §13.5/§13.6）。

    ① 共享 API key 常量时间比较（缺失/不符/未配置 → 401）；然后委托 submit_core。
    """
    # ① 共享 API key：header X-Api-Key 与 settings.tier_api_key 常量时间比较。
    #    settings.tier_api_key 为空（未配置）时也拒绝，避免空 key 被空 header 命中。
    expected = settings.tier_api_key or ""
    provided = x_api_key or ""
    if not expected or not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        return JSONResponse(status_code=401, content={"detail": "invalid api key"})

    try:
        result = submit_core(
            settings,
            requester_email=(body or {}).get("requester_email", ""),
            target_tier=(body or {}).get("target_tier", ""),
            reason=(body or {}).get("reason"),
        )
    except TierSubmitError as e:
        return JSONResponse(status_code=400, content={"detail": e.message})
    return result


@router.get("/api/tier-requests")
def list_requests(settings: Settings = Depends(get_settings)):
    """管理端列出全部调档申请（R66/R74；design §13.5/§13.6）。

    组鉴权由 main.py 中间件 + API GW JWT authorizer 负责（此处不做鉴权逻辑）。

    ① `scan` 全表（用 LastEvaluatedKey 翻页，汇总所有条目）；
    ② Lambda 内排序：`pending` 优先、再按 `created_at` 倒序（newest first）。
       Python sort 稳定：先按 created_at 倒序，再按「pending 优先」稳定排序，
       即得到「pending 组在前、组内 newest first；非 pending 组同样 newest first」。
    返回 `{"items": [...]}`。
    """
    table = _table(settings)

    # ① scan 全表（翻页汇总）。
    items: list[dict] = []
    scan_kwargs: dict = {}
    while True:
        resp = table.scan(**scan_kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    # ② 排序：先 created_at 倒序，再稳定地把 pending 提前（stable sort 保序）。
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    items.sort(key=lambda x: 0 if x.get("status") == "pending" else 1)

    # ③ 用部门表（identity-sync 写入，PK=user_key=邮箱小写）批量补全申请人姓名与部门：
    #    requester_name <- display_name；department <- department。查不到则置空。
    _enrich_requester_info(settings, items)

    return {"items": items}


@router.get("/api/tier-requests/mcp-config")
def mcp_config(request: Request, settings: Settings = Depends(get_settings)):
    """返回远程 MCP server 的配置信息（仅 admins，用于调档申请页展示分发步骤）。

    该端点受 admins 组校验保护（不在 _PUBLIC_PATHS；网关侧走 /api/{proxy+} 的
    JWT 授权路由），故可向已登录管理员回显共享 API key。
    端点 URL 从请求的 Host 头推导（API Gateway 域名），拼接 /mcp。
    返回：{ mcp_url, api_key, api_endpoint }。api_key 未配置时返回空串。
    """
    host = request.headers.get("host") or ""
    # API Gateway HTTP API 走 https；构造对外 endpoint。
    proto = request.headers.get("x-forwarded-proto") or "https"
    api_endpoint = f"{proto}://{host}" if host else ""
    return {
        "api_endpoint": api_endpoint,
        "mcp_url": f"{api_endpoint}/mcp" if api_endpoint else "/mcp",
        "api_key": settings.tier_api_key or "",
    }


def _enrich_requester_info(settings: Settings, items: list[dict]) -> None:
    """就地给每条申请补上 `requester_name` 与 `department`。

    数据源：`settings.dept_table`（user_department，PK=user_key=邮箱小写，含
    display_name/department，由 identity-sync 写入）。按 requester_email 去重后
    `batch_get_item` 查询（最多 100 键/批）。表未配置或查询失败时降级：
    字段置空字符串，不影响列表返回。
    """
    for it in items:
        it.setdefault("requester_name", "")
        it.setdefault("department", "")

    dept_table_name = settings.dept_table
    if not dept_table_name:
        return

    emails = sorted({(it.get("requester_email") or "").strip().lower()
                     for it in items if it.get("requester_email")})
    if not emails:
        return

    info: dict[str, dict] = {}
    try:
        ddb = _resource(settings.region)
        # batch_get_item 每批最多 100 键；分批并处理 UnprocessedKeys。
        for i in range(0, len(emails), 100):
            chunk = emails[i:i + 100]
            request = {dept_table_name: {"Keys": [{"user_key": e} for e in chunk]}}
            while request:
                resp = ddb.batch_get_item(RequestItems=request)
                for row in resp.get("Responses", {}).get(dept_table_name, []):
                    info[row.get("user_key", "")] = row
                request = resp.get("UnprocessedKeys") or {}
    except Exception as e:  # noqa: BLE001 —— 补全失败不影响列表返回
        log.warning("TIER_ENRICH 部门信息补全失败：%s", e)
        return

    for it in items:
        row = info.get((it.get("requester_email") or "").strip().lower())
        if row:
            it["requester_name"] = row.get("display_name") or ""
            it["department"] = row.get("department") or ""


# 关闭结果 -> 双语邮件主题/正文片段。close 时按 outcome 选取对应文案，
# 追加 target_tier；有 admin_note 时补一段「管理员备注 / Admin note」。
_CLOSE_EMAIL = {
    "completed": {
        "subject": "您的 Kiro 调档申请已完成 / Your Kiro tier change request is completed",
        "zh": "您好，\n\n您申请调整到「{tier}」档位的请求已处理完成。",
        "en": "Hello,\n\nYour request to change to the \"{tier}\" tier has been completed.",
    },
    "rejected": {
        "subject": "您的 Kiro 调档申请未通过 / Your Kiro tier change request was rejected",
        "zh": "您好，\n\n很抱歉，您申请调整到「{tier}」档位的请求未获通过。",
        "en": "Hello,\n\nWe are sorry, but your request to change to the \"{tier}\" tier was not approved.",
    },
}


def _build_close_email(outcome: str, target_tier: str, admin_note: Optional[str]) -> tuple[str, str, str]:
    """按结果状态构造 close 通知邮件的 (subject, text, html)；双语、含可选 admin_note。"""
    tpl = _CLOSE_EMAIL[outcome]
    tier = target_tier or "-"
    zh = tpl["zh"].format(tier=tier)
    en = tpl["en"].format(tier=tier)

    note = (admin_note or "").strip()
    zh_note = f"\n\n管理员备注：{note}" if note else ""
    en_note = f"\n\nAdmin note: {note}" if note else ""

    text = f"{zh}{zh_note}\n\n———\n\n{en}{en_note}\n"

    def _esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\n", "<br>"))

    note_html_zh = f"<p><b>管理员备注：</b>{_esc(note)}</p>" if note else ""
    note_html_en = f"<p><b>Admin note:</b> {_esc(note)}</p>" if note else ""
    html = (
        "<html><body style=\"font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1f2937;\">"
        f"<p>{_esc(zh)}</p>{note_html_zh}"
        "<hr style=\"border:none;border-top:1px solid #eee;margin:16px 0;\">"
        f"<p>{_esc(en)}</p>{note_html_en}"
        "<p style=\"color:#9ca3af;font-size:12px;margin-top:20px;\">本邮件由系统自动发送。This is an automated message.</p>"
        "</body></html>"
    )
    return tpl["subject"], text, html


def _send_close_email(settings: Settings, to_email: str, outcome: str,
                      target_tier: str, admin_note: Optional[str]) -> dict:
    """向申请人发送 close 结果通知邮件（复用 report handler 的 SES 口径）。

    - 发件人/region 取 `settings.ses_sender` / `settings.region`；发件人未配置 → 不发。
    - SES 已移出沙盒（R71），不做 report Lambda 的「已验证收件人过滤」，直接发任意收件人。
    - 发送失败不抛出（与 report handler 一致），返回 {"sent": False, "error": ...}，
      不让整个 close 请求失败。
    返回 {"sent": bool, "message_id": <id|None>, ...}。
    """
    sender = settings.ses_sender or ""
    if not sender:
        return {"sent": False, "reason": "SES_SENDER 未配置"}
    try:
        subject, text, html = _build_close_email(outcome, target_tier, admin_note)
        ses = boto3.client("ses", region_name=settings.region)
        resp = ses.send_email(
            Source=sender,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text, "Charset": "UTF-8"},
                    "Html": {"Data": html, "Charset": "UTF-8"},
                },
            },
        )
        return {"sent": True, "message_id": resp.get("MessageId")}
    except Exception as e:  # noqa: BLE001 —— 发送失败不应让 close 请求整体失败
        return {"sent": False, "error": str(e)}


@router.post("/api/tier-requests/{id}/close")
def close(
    request: Request,
    id: str = Path(...),
    body: dict = Body(...),
    settings: Settings = Depends(get_settings),
):
    """管理端关闭一条调档申请（R67–R71；design §13.5/§13.6）。

    组鉴权由 main.py 中间件 + API GW JWT authorizer 负责（此处只取 actor 审计）。

    ① `outcome` 必须为 completed|rejected，否则 400；
    ② `get_item` 取记录，不存在 → 404；
    ③ `update_item`：SET status=outcome、processed_at=now、processed_by=_actor，
       可选 admin_note（#s 别名规避保留字 status）；
    ④ 发 SES 双语、按状态区分主题/正文的邮件到 requester_email（含 admin_note 段落）；
    ⑤ 返回 {"ok", "id", "status", "email": {...}}。
    """
    # ① outcome 校验。
    outcome = (body or {}).get("outcome")
    if outcome not in ("completed", "rejected"):
        return JSONResponse(status_code=400,
                            content={"detail": "outcome 必须为 completed 或 rejected"})

    admin_note = (body or {}).get("admin_note")

    table = _table(settings)

    # ② 取记录（不存在 → 404）。
    got = table.get_item(Key={"id": id})
    item = got.get("Item")
    if not item:
        return JSONResponse(status_code=404, content={"detail": "申请不存在"})

    actor = _actor(request)

    # ③ update_item：写状态 + processed_*（+ 可选 admin_note）。#s 规避保留字 status。
    set_parts = ["#s = :o", "processed_at = :at", "processed_by = :by"]
    names = {"#s": "status"}
    values = {":o": outcome, ":at": _now_iso(), ":by": actor}
    if admin_note is not None:
        set_parts.append("admin_note = :note")
        values[":note"] = admin_note
    table.update_item(
        Key={"id": id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )

    # ④ SES 通知申请人（状态匹配主题/正文，含 admin_note）；失败不阻断 close。
    email_res = _send_close_email(
        settings, item.get("requester_email", ""), outcome,
        item.get("target_tier", ""), admin_note)

    # 结构化审计：actor=处理人（JWT）、target=申请 id、extra 记结果/邮件/申请人。
    _audit(actor, "close", id,
           {"outcome": outcome, "email_sent": bool(email_res.get("sent")),
            "requester_email": item.get("requester_email", "")})

    return {
        "ok": True,
        "id": id,
        "status": outcome,
        "email": {"sent": bool(email_res.get("sent")),
                  "message_id": email_res.get("message_id")},
    }
