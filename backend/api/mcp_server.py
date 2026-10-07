"""Remote MCP server（Streamable HTTP）—— 无状态手写实现，适配 Lambda。

把本地 stdio MCP server（mcp/tier_request_server/）替换为**托管的远程 MCP 端点**：
用户 `mcp.json` 只需填一个 URL + header，无需在本机下载代码/装依赖，逻辑集中在
服务端，版本管控 = 重部署。

为什么手写而不用 mcp SDK 的 HTTP server：
    SDK 的 `streamable_http_app()` + `StreamableHTTPSessionManager` 假设**常驻进程**
    （task group 由 ASGI lifespan 启动，且 `.run()` 每实例只能调一次）。而 Lambda+Mangum
    每次调用都会重跑 lifespan，触发 "run() can only be called once" → 500。单工具的
    MCP-over-HTTP 协议面很小（initialize / notifications/initialized / tools/list /
    tools/call），故改为**无状态**的普通 FastAPI 路由：每个 POST 独立处理一条 JSON-RPC
    消息，返回单个 JSON 响应，无 session、无 SSE、无 lifespan —— 完美契合 Lambda，且与
    MCP Streamable HTTP 线协议兼容（Kiro 以 `url` 方式连接不变）。

鉴权与身份（对齐已接受的威胁模型，R48–R50）：
- 工具调用需带共享 `X-Api-Key`（与 settings.tier_api_key 常量时间比较）；
- `requester_email` 由调用方作为工具参数传入（非验证身份令牌，可伪填）；管理员
  人工审批为最终背书。
"""
from __future__ import annotations

import hmac
import logging
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

try:
    from backend.api.settings import get_settings
    from backend.api.tier_requests import submit_core, TierSubmitError
except ImportError:  # Lambda 平铺打包
    from settings import get_settings  # type: ignore
    from tier_requests import submit_core, TierSubmitError  # type: ignore

log = logging.getLogger("mcp_server")

# 本服务对外声明的协议版本（与 Kiro 当前对接的 Streamable HTTP 稳定版一致）。
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "kiro-tier-request"
SERVER_VERSION = "1.0.0"
_ALLOWED = "Pro / Pro+ / Pro Max / Power"

router = APIRouter(tags=["mcp"])

# 单工具定义（tools/list 与 tools/call 共用）。inputSchema 为标准 JSON Schema。
_TOOL = {
    "name": "request_tier_change",
    "description": ("申请变更 Kiro 订阅档位（Pro / Pro+ / Pro Max / Power）。申请人邮箱通常由 "
                    "MCP 配置的 X-Requester-Email 头自动提供，无需在对话里提供；若未配置该头，"
                    "再传 requester_email 参数。"),
    "inputSchema": {
        "type": "object",
        "properties": {
            "target_tier": {
                "type": "string",
                "description": "目标档位。接受常见拼写与大小写（如 pro+、Pro Max、promax）。",
            },
            "requester_email": {
                "type": "string",
                "description": "（可选）申请人邮箱。通常由 MCP 配置的 X-Requester-Email 头提供；"
                               "仅当未配置该头时才需在此传入。",
            },
            "reason": {"type": "string", "description": "可选的申请理由。"},
        },
        "required": ["target_tier"],
    },
}


def _rpc_result(id_: Any, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})


def _rpc_error(id_: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def _tool_text(text: str, is_error: bool = False) -> dict:
    """tools/call 的标准返回：content[].text + structuredContent。"""
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"result": text},
        "isError": is_error,
    }


def _check_api_key(request: Request) -> bool:
    """常量时间比较请求头 X-Api-Key 与 settings.tier_api_key。未配置/不符 → False。"""
    settings = get_settings()
    expected = settings.tier_api_key or ""
    provided = request.headers.get("x-api-key") or ""
    if not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _call_request_tier_change(request: Request, args: dict) -> dict:
    """执行 request_tier_change 工具，返回 tools/call result。

    申请人身份来源（优先级）：
      ① 请求头 `X-Requester-Email`（来自用户 mcp.json 的 headers，等价于本地版
         的 env REQUESTER_EMAIL——每台机器配一次，模型无需每次填）；
      ② 工具参数 `requester_email`（回退；便于临时指定或不配 header 的场景）。
    两者都没有 → 友好报错。注：header 邮箱与本地版 env 一样，**非验证身份令牌**，
    管理员人工审批兼底（R48–R50）。
    """
    settings = get_settings()
    target_tier = args.get("target_tier", "")
    reason = args.get("reason")
    # ① header 优先；② 回退到工具参数。
    requester_email = (request.headers.get("x-requester-email")
                       or args.get("requester_email") or "")
    try:
        res = submit_core(settings, requester_email=requester_email,
                          target_tier=target_tier, reason=reason)
    except TierSubmitError as e:
        if e.field == "target_tier":
            return _tool_text(f"档位无效，请选择 {_ALLOWED}。", is_error=True)
        return _tool_text(
            "申请人邮箱无效或缺失：请在 MCP 配置的 headers 里设 `X-Requester-Email`，"
            "或在请求里提供 requester_email。", is_error=True)
    except Exception as e:  # noqa: BLE001
        log.warning("MCP request_tier_change 失败：%s", e)
        return _tool_text("提交失败：服务端错误，请稍后重试或联系管理员。", is_error=True)

    note = f"（已取代 {res['superseded']} 条旧的待处理申请）" if res.get("superseded") else ""
    return _tool_text(f"已提交调档申请：{res['status']} · {target_tier}{note}，等待管理员处理。")


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    """无状态 MCP Streamable HTTP 端点：处理单条 JSON-RPC 消息。

    支持方法：initialize、notifications/initialized（通知，无响应）、tools/list、
    tools/call。其余方法返回 JSON-RPC -32601（Method not found）。
    """
    try:
        msg = await request.json()
    except Exception:  # noqa: BLE001
        return _rpc_error(None, -32700, "Parse error")

    # 批量消息（list）：本服务一问一答，仅取首条（Kiro 单条发送）。
    if isinstance(msg, list):
        msg = msg[0] if msg else {}
    if not isinstance(msg, dict):
        return _rpc_error(None, -32600, "Invalid Request")

    method = msg.get("method")
    msg_id = msg.get("id")

    # 通知（无 id）——如 notifications/initialized：按规范不返回响应体，202 即可。
    if msg_id is None and (method or "").startswith("notifications/"):
        return JSONResponse(status_code=202, content=None)

    if method == "initialize":
        return _rpc_result(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": [_TOOL]})

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name != _TOOL["name"]:
            return _rpc_error(msg_id, -32602, f"Unknown tool: {name}")
        # 工具层共享 API key 校验（header X-Api-Key）。
        if not _check_api_key(request):
            return _rpc_result(msg_id, _tool_text(
                "鉴权失败：缺少或不正确的 API Key，请检查 MCP 配置中的 X-Api-Key。", is_error=True))
        return _rpc_result(msg_id, _call_request_tier_change(request, args))

    # ping（部分客户端会发）：返回空 result。
    if method == "ping":
        return _rpc_result(msg_id, {})

    return _rpc_error(msg_id, -32601, f"Method not found: {method}")
