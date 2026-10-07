"""FastAPI 依赖：查询层、身份、identity_filter 钩子。

- 生产：QueryLayer 走真实 Athena runner。
- 测试：通过 app.dependency_overrides 注入 mock QueryLayer。
- identity_filter：v1 管理员=None（全量）；N3 预留——当 enforce_identity_filter=true
  时，从 JWT claims 强制注入 email，Agent/前端无法覆盖。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from fastapi import Depends, Request

try:
    from backend.shared.athena_client import Queries, QueryLayer, make_athena_runner
except ImportError:  # Lambda 内平铺打包
    from shared.athena_client import Queries, QueryLayer, make_athena_runner  # type: ignore

try:
    from backend.api.settings import Settings, get_settings
except ImportError:
    from settings import Settings, get_settings  # type: ignore


@lru_cache(maxsize=1)
def _real_query_layer() -> QueryLayer:
    s = get_settings()
    runner = make_athena_runner(
        database=s.glue_db, workgroup=s.athena_workgroup, region=s.region)
    return QueryLayer(runner, cache_ttl=s.cache_ttl)


def get_queries(settings: Settings = Depends(get_settings)) -> Queries:
    return Queries(db=settings.glue_db)


def get_query_layer() -> QueryLayer:
    """默认返回真实查询层；测试用 dependency_overrides 覆盖。"""
    return _real_query_layer()


def get_claims(request: Request) -> dict:
    """从 API Gateway JWT authorizer 注入的上下文提取 Cognito claims。

    HTTP API (payload v2) 下位于:
      event.requestContext.authorizer.jwt.claims
    Mangum 会把它放到 request.scope['aws.event']。
    """
    event = request.scope.get("aws.event") or {}
    rc = (event.get("requestContext") or {})
    authz = (rc.get("authorizer") or {})
    jwt = (authz.get("jwt") or {})
    return jwt.get("claims") or {}


def get_identity_filter(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Optional[dict]:
    """v1：管理员=None（全量）。v2：enforce 时按 token email 过滤。"""
    if not settings.enforce_identity_filter:
        return None
    claims = get_claims(request)
    email = claims.get("email")
    return {"email": email} if email else {"email": "__no_such_user__"}
