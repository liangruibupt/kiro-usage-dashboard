"""调档建议的单一真实来源（邮件报告与报告页 API 共用，保证口径一致）。

口径（“套餐匹配”模型）：
- Kiro 有四个套餐档位，各含固定 credit 额度与月费：
  Pro=1000credit/$20 · Pro+=2000/$40 · Pro Max=5000/$100 · Power=10000/$200
- 根据用户当月**实际用量**（credits_used），找到能覆盖该用量的**最便宜档位**
  （required tier = 满足 usage <= 额度 的最低档；用量超过 Power 额度则取最高档 Power）。
- 将 required tier 与用户**当前档位**比较：
    required 更低 -> 建议降级（下月降到 required，可跨多档）
    required 更高 -> 建议升级（下月升到 required，可跨多档）
    required 相同 -> 保持，不建议
- 无用量（credits_used<=0）的用户不建议（视为保持）。

示例（当前 Pro+/$40，额度 2000）：
  用量 <= 1000 -> 降级到 Pro；1000 < 用量 <= 2000 -> 保持；用量 > 2000 -> 升级到 Pro Max。
"""
from __future__ import annotations

from typing import Optional

# 档位阶梯（低 -> 高）
TIER_LADDER = ["Pro", "ProPlus", "ProMax", "Power"]
# 档位 -> 展示名
TIER_ZH = {"Pro": "Pro", "ProPlus": "Pro+", "ProMax": "Pro Max", "Power": "Power"}

# 各档位 credit 额度（低 -> 高，与 TIER_LADDER 对应）
TIER_LIMITS: dict[str, int] = {
    "Pro": 1000,
    "ProPlus": 2000,
    "ProMax": 5000,
    "Power": 10000,
}
# 各档位月费（美元），用于展示/说明
TIER_PRICE_USD: dict[str, int] = {
    "Pro": 20,
    "ProPlus": 40,
    "ProMax": 100,
    "Power": 200,
}


def tier_label(key: str, with_price: bool = False) -> str:
    """档位显示名（Kiro 官方叫法）；with_price 时追加月费，如 "Power ($200)"。"""
    name = TIER_ZH.get(key, key)
    price = TIER_PRICE_USD.get(key)
    return f"{name} (${price})" if with_price and price is not None else name


def next_tier(tier: str) -> Optional[str]:
    """升一档；已在最高档返回 None。"""
    if tier not in TIER_LADDER:
        return None
    i = TIER_LADDER.index(tier)
    return TIER_LADDER[i + 1] if i < len(TIER_LADDER) - 1 else None


def prev_tier(tier: str) -> Optional[str]:
    """降一档；已在最低档返回 None。"""
    if tier not in TIER_LADDER:
        return None
    i = TIER_LADDER.index(tier)
    return TIER_LADDER[i - 1] if i > 0 else None


def required_tier(credits_used: float) -> str:
    """能覆盖该用量的最便宜档位；用量超过最高档额度时返回最高档 Power。"""
    for tier in TIER_LADDER:  # 低 -> 高
        if credits_used <= TIER_LIMITS[tier]:
            return tier
    return TIER_LADDER[-1]  # 超过 Power 额度：无更高档可升，取 Power


def annotate_tier_steps(rows: list[dict]) -> list[dict]:
    """给每行附加「升一档/降一档」建议键（使用率阈值模型用）：
       suggested_up   = next_tier(subscription_tier)  # 已在最高档 Power -> None
       suggested_down = prev_tier(subscription_tier)  # 已在最低档 Pro  -> None
    当前档未知（不在 TIER_LADDER）时两者都为 None。原行其余字段原样透传。
    """
    out = []
    for r in rows:
        tier = r.get("subscription_tier") or ""
        out.append({
            **r,
            "suggested_up": next_tier(tier),
            "suggested_down": prev_tier(tier),
        })
    return out


def recommendations(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """从每用户行（含 subscription_tier / credits_used）计算
    (建议升档列表, 建议降档列表)。每项在原行基础上加 suggested_tier。

    逻辑（套餐匹配）：required = 覆盖当月用量的最便宜档位；
      required 高于当前档 -> 升档；低于 -> 降档；相同 -> 保持。
      无用量（credits_used<=0）或当前档未知的用户不建议。
    """
    upgrades, downgrades = [], []
    for r in rows:
        tier = r.get("subscription_tier") or ""
        used = float(r.get("credits_used") or 0)
        if tier not in TIER_LADDER or used <= 0:
            continue
        req = required_tier(used)
        cur_i = TIER_LADDER.index(tier)
        req_i = TIER_LADDER.index(req)
        if req_i > cur_i:
            upgrades.append({**r, "suggested_tier": req})
        elif req_i < cur_i:
            downgrades.append({**r, "suggested_tier": req})
        # req_i == cur_i：保持，不建议
    # 升档：用量越高越靠前；降档：用量越低越靠前
    upgrades.sort(key=lambda x: float(x.get("credits_used") or 0), reverse=True)
    downgrades.sort(key=lambda x: float(x.get("credits_used") or 0))
    return upgrades, downgrades
