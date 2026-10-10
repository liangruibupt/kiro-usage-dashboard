"""派生字段口径（单一真实来源）。

口径（基于真实 Kiro 数据校准）：
- usage_rate = Credits_Used / Usage_Limit（套餐额度，PRO=1000/PRO_MAX=5000/POWER=10000）
- balance    = Usage_Limit - Credits_Used（下限 0）
- 成本（方案 A，对齐真实账单量级）：
    成本 = 订阅月费（按档位，整月，每席位固定）+ overage 成本（仅超额部分）
    * overage 成本 = credit_unit_price * Overage_Credits_Used（真实计费的超额 credits）
      —— 注意：**不是** credits_used * 单价。$0.04/credit 只适用于超出套餐额度、
      且开启了 overage 的那部分；套餐额度内的 credits 已含在月费里。
    * 未开启 overage 的客户 Overage_Credits_Used=0，overage 成本自然为 0。
  月费不在日表逐行累加（否则一个月会重复加 30 次），而是在聚合查询里
  按席位（每 email 的档位）加一次；日表的 est_cost 只放**当天的 overage 成本**。

注：早期曾用 est_cost = credits_used * $0.04，把套餐内含 credits 也当超额计费，
导致总成本严重高估（9 月 $40k vs 真实订阅 ~$27k）。现改为方案 A。

所有需要派生字段的地方（ETL / API / Agent）都从这里取，避免口径漂移。
"""
from __future__ import annotations

DEFAULT_CREDIT_UNIT_PRICE = 0.04  # Kiro 官方超额/加购 credit 单价

# 档位月费（USD/席位/月，Kiro 官方价）。单一真实来源，与前端 TIER_PRICE_USD 对齐。
# key 为规范化后的档位名（Pro/ProPlus/ProMax/Power）。
TIER_MONTHLY_USD: dict[str, float] = {
    "Pro": 20.0,
    "ProPlus": 40.0,
    "ProMax": 100.0,
    "Power": 200.0,
}


def tier_monthly_cost(subscription_tier: str) -> float:
    """按档位返回整月订阅月费（USD）。未知档位返回 0（无法估价，不臆造）。"""
    return TIER_MONTHLY_USD.get((subscription_tier or "").strip(), 0.0)


def overage_cost(overage_credits_used: float,
                 credit_unit_price: float = DEFAULT_CREDIT_UNIT_PRICE) -> float:
    """超额成本 = 真实计费的超额 credits * 单价。未开 overage 时输入为 0 -> 0。"""
    return round(max(overage_credits_used or 0.0, 0.0) * credit_unit_price, 2)


def usage_rate(credits_used: float, usage_limit: float) -> float:
    """使用率 = 已用 / 套餐额度。额度为 0 时返回 0。"""
    if not usage_limit or usage_limit <= 0:
        return 0.0
    return round(credits_used / usage_limit, 4)


def balance(credits_used: float, usage_limit: float) -> float:
    """余额 = 额度 - 已用，展示口径不小于 0。"""
    return round(max(usage_limit - credits_used, 0.0), 1)


def is_overage(credits_used: float, usage_limit: float) -> bool:
    """是否超额 = 已用 credits 超过套餐额度（对齐 Kiro 控制台口径）。"""
    return bool(usage_limit and usage_limit > 0 and credits_used > usage_limit)
