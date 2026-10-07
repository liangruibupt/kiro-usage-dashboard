"""管理员管理接口（前缀 /api/admin）。

- 仅 admins 组可调（应用层组校验已在 main.py 中间件；此处再取 actor 用于审计）。
- 用 boto3 cognito-idp 对本 User Pool 做受控管理操作。
- 每个写操作记结构化审计日志到 CloudWatch。
- 不接受任意入参：邮箱格式校验；User Pool ID 来自环境（不可被请求覆盖）。

对应 requirements R32–R40、design §12.1。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import boto3
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi.responses import JSONResponse

try:
    from backend.api.settings import Settings, get_settings
except ImportError:  # Lambda 平铺打包
    from settings import Settings, get_settings  # type: ignore

log = logging.getLogger("admin_users")
router = APIRouter(prefix="/api/admin", tags=["admin"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _cognito(region: str):
    return boto3.client("cognito-idp", region_name=region)


def _actor(request: Request) -> str:
    """从 JWT claims 取调用者标识（email 优先，退 sub），用于审计。"""
    event = request.scope.get("aws.event") or {}
    claims = ((((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {})
              .get("claims") or {})
    return claims.get("email") or claims.get("sub") or "unknown"


def _audit(actor: str, action: str, target: str, extra: Optional[dict] = None) -> None:
    log.info("ADMIN_AUDIT %s", json.dumps(
        {"actor": actor, "action": action, "target": target, **(extra or {})},
        ensure_ascii=False))


def _valid_email(email: str) -> Optional[str]:
    e = (email or "").strip().lower()
    return e if _EMAIL_RE.match(e) else None


def _status_of(u: dict) -> dict:
    """把 Cognito 用户对象归一化为 {email, status, enabled}。"""
    attrs = {a["Name"]: a["Value"] for a in u.get("Attributes", u.get("UserAttributes", []))}
    return {
        "email": attrs.get("email") or u.get("Username", ""),
        "status": u.get("UserStatus", ""),
        "enabled": u.get("Enabled", True),
        "created_at": str(u.get("UserCreateDate")) if u.get("UserCreateDate") else None,
    }


@router.get("/users")
def list_admins(settings: Settings = Depends(get_settings)):
    if not settings.user_pool_id:
        return JSONResponse(status_code=503, content={"detail": "USER_POOL_ID 未配置"})
    cog = _cognito(settings.region)
    users, token = [], None
    while True:
        kw = {"UserPoolId": settings.user_pool_id, "GroupName": settings.required_group, "Limit": 60}
        if token:
            kw["NextToken"] = token
        resp = cog.list_users_in_group(**kw)
        users.extend(_status_of(u) for u in resp.get("Users", []))
        token = resp.get("NextToken")
        if not token:
            break
    users.sort(key=lambda x: x["email"].lower())
    return {"users": users}


@router.post("/users")
def create_admin(request: Request, body: dict = Body(...),
                 settings: Settings = Depends(get_settings)):
    if not settings.user_pool_id:
        return JSONResponse(status_code=503, content={"detail": "USER_POOL_ID 未配置"})
    email = _valid_email((body or {}).get("email", ""))
    if not email:
        return JSONResponse(status_code=400, content={"detail": "邮箱格式无效"})
    cog = _cognito(settings.region)
    actor = _actor(request)
    try:
        # 不加 MessageAction=SUPPRESS：Cognito 自动发含临时密码的邀请邮件
        cog.admin_create_user(
            UserPoolId=settings.user_pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
        )
        cog.admin_add_user_to_group(
            UserPoolId=settings.user_pool_id, Username=email,
            GroupName=settings.required_group)
    except cog.exceptions.UsernameExistsException:
        # 已存在则仅确保在 admins 组
        cog.admin_add_user_to_group(
            UserPoolId=settings.user_pool_id, Username=email,
            GroupName=settings.required_group)
        _audit(actor, "create_admin(existing->add_group)", email)
        return {"ok": True, "note": "用户已存在，已确保加入 admins 组"}
    _audit(actor, "create_admin", email)
    return {"ok": True}


@router.post("/users/{email}/reset-password")
def reset_password(request: Request, email: str = Path(...),
                   settings: Settings = Depends(get_settings)):
    if not settings.user_pool_id:
        return JSONResponse(status_code=503, content={"detail": "USER_POOL_ID 未配置"})
    e = _valid_email(email)
    if not e:
        return JSONResponse(status_code=400, content={"detail": "邮箱格式无效"})
    cog = _cognito(settings.region)
    cog.admin_reset_user_password(UserPoolId=settings.user_pool_id, Username=e)
    _audit(_actor(request), "reset_password", e)
    return {"ok": True}


@router.post("/users/{email}/resend-invite")
def resend_invite(request: Request, email: str = Path(...),
                  settings: Settings = Depends(get_settings)):
    if not settings.user_pool_id:
        return JSONResponse(status_code=503, content={"detail": "USER_POOL_ID 未配置"})
    e = _valid_email(email)
    if not e:
        return JSONResponse(status_code=400, content={"detail": "邮箱格式无效"})
    cog = _cognito(settings.region)
    # RESEND 仅对 FORCE_CHANGE_PASSWORD（未激活）用户有效，重发含临时密码的邀请邮件
    cog.admin_create_user(
        UserPoolId=settings.user_pool_id, Username=e, MessageAction="RESEND")
    _audit(_actor(request), "resend_invite", e)
    return {"ok": True}


@router.delete("/users/{email}")
def remove_admin(request: Request, email: str = Path(...),
                 settings: Settings = Depends(get_settings)):
    if not settings.user_pool_id:
        return JSONResponse(status_code=503, content={"detail": "USER_POOL_ID 未配置"})
    e = _valid_email(email)
    if not e:
        return JSONResponse(status_code=400, content={"detail": "邮箱格式无效"})
    cog = _cognito(settings.region)
    cog.admin_remove_user_from_group(
        UserPoolId=settings.user_pool_id, Username=e,
        GroupName=settings.required_group)
    _audit(_actor(request), "remove_admin", e)
    return {"ok": True}
