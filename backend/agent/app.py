"""AgentCore Runtime 入口。

BedrockAgentCoreApp 自动暴露符合 AgentCore Runtime 协议的 `/invocations` 与 `/ping`。
入站鉴权（Cognito JWT）由 AgentCore Runtime 的 Inbound Identity 在平台层完成，
容器内无需再校验签名；这里聚焦于把用户问题交给 Strands Agent 并返回回答。

调用契约：
  请求 body: {"prompt": "上周哪个部门 Credits 最高？", "session_id": "..."}
  响应:      {"reply": "...", "session_id": "..."}
"""
from __future__ import annotations

from bedrock_agentcore.runtime import BedrockAgentCoreApp

try:
    from agent import build_agent
except ImportError:
    from backend.agent.agent import build_agent  # type: ignore

app = BedrockAgentCoreApp()
_agent = build_agent()


@app.entrypoint
def invoke(payload: dict) -> dict:
    prompt = (payload or {}).get("prompt") or (payload or {}).get("input") or ""
    if not prompt:
        return {"reply": "请提供一个用量相关的问题，例如：本月哪个部门 Credits 消耗最高？"}
    result = _agent(prompt)
    # strands Agent 返回结果对象，str() 取最终文本
    reply = str(result)
    out = {"reply": reply}
    if payload and payload.get("session_id"):
        out["session_id"] = payload["session_id"]
    return out


if __name__ == "__main__":
    app.run()
