"""Lambda 入口：用 Mangum 适配 FastAPI 到 API Gateway HTTP API (payload v2)。"""
from __future__ import annotations

from mangum import Mangum

try:
    from backend.api.main import app
except ImportError:  # Lambda 平铺打包
    from main import app  # type: ignore

# 远程 MCP 端点改为无状态手写路由（无 ASGI lifespan 依赖），故 lifespan="off"。
handler = Mangum(app, lifespan="off")
