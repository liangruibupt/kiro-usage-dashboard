"""Kiro tier-request MCP server (stdio).

Exposes a single tool ``request_tier_change`` to the Kiro IDE. The tool
normalizes a requested tier against an allow-list, and on success POSTs a
tier-change request to the dashboard submission endpoint with the shared
``X-Api-Key`` header.

Runnable as: ``python -m tier_request_server.server``

Environment variables (R52):
  - REQUESTER_EMAIL        本机用户身份，作为 requester_email 提交
  - API_KEY                与后端共享的 API key（X-Api-Key 头）
  - TIER_REQUEST_ENDPOINT  提交端点完整 URL（POST /api/tier-requests）
"""

import json
import os
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP

# 允许清单 + 别名归一化（与后端 _normalize_tier 同表）（R53）。
_ALIASES = {
    "pro": "Pro",
    "pro+": "ProPlus",
    "proplus": "ProPlus",
    "pro plus": "ProPlus",
    "promax": "ProMax",
    "pro max": "ProMax",
    "pro-max": "ProMax",
    "power": "Power",
}


def _normalize(raw: str):
    """Normalize a raw tier string to a canonical tier, or ``None`` if invalid.

    Accepts common spellings/casing (e.g. ``pro+``, ``Pro Max``, ``promax``);
    unknown values return ``None`` so the caller can reject without a request.
    """
    return _ALIASES.get((raw or "").strip().lower().replace("_", " "))


mcp = FastMCP("kiro-tier-request")


@mcp.tool()
def request_tier_change(target_tier: str, reason: str | None = None) -> str:
    """申请变更 Kiro 订阅档位（Pro / Pro+ / Pro Max / Power）。"""
    tier = _normalize(target_tier)
    if not tier:
        # 非法档位：返回友好错误且不发请求（R54）。
        return "档位无效，请选择 Pro / Pro+ / Pro Max / Power。"
    # 归一化通过后携带 X-Api-Key 头 POST 提交（R55）。
    payload = {"requester_email": os.environ["REQUESTER_EMAIL"], "target_tier": tier}
    if reason:
        payload["reason"] = reason
    req = urllib.request.Request(
        os.environ["TIER_REQUEST_ENDPOINT"],
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Api-Key": os.environ["API_KEY"]},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            json.loads(r.read().decode())
        return f"已提交调档申请：{tier}，等待管理员处理。"
    except urllib.error.HTTPError as e:
        return f"提交失败（{e.code}）：请联系管理员或稍后重试。"
    except urllib.error.URLError as e:
        return f"提交失败：网络不可达（{e.reason}），请稍后重试。"


if __name__ == "__main__":
    mcp.run()  # stdio
