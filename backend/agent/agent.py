"""Kiro 用量 NL 问答 Agent（Strands + Bedrock）。

- 系统提示约束：只用受控工具、引用具体数值与时间范围、数据不足时说明（R27）。
- 模型走 Bedrock（默认 Claude Sonnet）。
- 不接受任意 SQL；所有数据访问经 tools.py 的参数化工具。
"""
from __future__ import annotations

import os

from strands import Agent
from strands.models import BedrockModel

try:
    from tools import ALL_TOOLS
except ImportError:
    from backend.agent.tools import ALL_TOOLS  # type: ignore

# 用跨区域推理配置 ID（on-demand 直接调用 sonnet-4.5 不受支持，需 inference profile）。
MODEL_ID = os.environ.get(
    "AGENT_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
REGION = os.environ.get("AWS_REGION", os.environ.get("DEPLOY_REGION", "us-west-2"))

SYSTEM_PROMPT = """你是「Kiro AI 使用效能看板」的用量分析助手，服务于企业管理员。

职责与规则：
- 优先用通用工具 query_usage 回答绝大多数问题，把自然语言映射为参数：
  metric(credits/messages/est_cost/active_days/users) × group_by(none/department/
  subscription_tier/model/date/user) × 过滤(department/subscription_tier/model/user/overage_only)
  × 时间(period 或 date_from/date_to)。
- 某个人用了哪些模型：若只知中文名，先调 find_user 拿到 email，再调
  query_usage(metric=messages, group_by=model, user=<email>)。整体 KPI 用 get_summary。
- 只能通过提供的受控工具查询数据，绝不编造数字，也不执行任意 SQL。
- 回答时必须引用具体数值，并说明所用的时间范围与口径（例如"2026-09-01 至 2026-09-07，按用户聚合"）。
- 若用户问题缺少时间范围，可先用 get_filters 了解可用周期，或向用户澄清；不要臆测。
- 【年份】用户只说“9月”这类不带年份时，不要自己猜年份（如 2024）；先调 get_filters 看真实存在的周期
  （如 2026-09），用那个年月。找人用 find_user（它不受时间限制），拿到 email 后再按月份查用量。
- 口径：messages=提问次数（非 credit）；使用率=已用/套餐额度；超额=区间累计 credits>额度；预估成本=单价×已用 Credits（属估算）。
- 数据不足或工具未覆盖时，明确说明，不要给出无依据的结论。
- 用简洁的中文回答，必要时用列表或小表格呈现关键数字。
"""


def build_agent(system_prompt: str = SYSTEM_PROMPT) -> Agent:
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    return Agent(model=model, tools=ALL_TOOLS, system_prompt=system_prompt)
