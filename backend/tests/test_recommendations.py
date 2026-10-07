"""升/降档建议共享模块单测（邮件报告与报告页 API 共用同一算法）。

新口径“套餐匹配”：根据当月实际用量找到能覆盖的最便宜档位（required），
与当前档位比较后给出升/降/保持建议。
"""
from backend.shared.recommendations import (
    recommendations, next_tier, prev_tier, required_tier,
    TIER_LIMITS, TIER_PRICE_USD, tier_label,
)


def test_tier_ladder_edges():
    assert next_tier("Pro") == "ProPlus"
    assert next_tier("ProMax") == "Power"
    assert next_tier("Power") is None          # 最高档不升
    assert prev_tier("Power") == "ProMax"
    assert prev_tier("Pro") is None            # 最低档不降
    assert next_tier("Unknown") is None


def test_tier_limits_and_prices():
    assert TIER_LIMITS == {"Pro": 1000, "ProPlus": 2000, "ProMax": 5000, "Power": 10000}
    assert TIER_PRICE_USD == {"Pro": 20, "ProPlus": 40, "ProMax": 100, "Power": 200}


def test_tier_label_official_names_and_price():
    # 官方叫法（不带价）
    assert tier_label("Pro") == "Pro"
    assert tier_label("ProPlus") == "Pro+"
    assert tier_label("ProMax") == "Pro Max"
    assert tier_label("Power") == "Power"
    # 带价
    assert tier_label("ProPlus", True) == "Pro+ ($40)"
    assert tier_label("Power", True) == "Power ($200)"
    # 未知 key 原样返回，不拼价
    assert tier_label("Unknown", True) == "Unknown"


def test_required_tier_boundaries():
    # 边界：恰好等于额度算“覆盖”
    assert required_tier(1) == "Pro"
    assert required_tier(1000) == "Pro"
    assert required_tier(1000.01) == "ProPlus"
    assert required_tier(2000) == "ProPlus"
    assert required_tier(2000.5) == "ProMax"
    assert required_tier(5000) == "ProMax"
    assert required_tier(5000.1) == "Power"
    assert required_tier(10000) == "Power"
    assert required_tier(999999) == "Power"    # 超过最高档：仍取 Power


def test_proplus_example_from_user():
    """用户给的 Pro+/$40 例子：<=1000 降 Pro；1000~2000 保持；>2000 升 Pro Max。"""
    rows = [
        {"subscription_tier": "ProPlus", "credits_used": 900},   # <=1000 -> 降 Pro
        {"subscription_tier": "ProPlus", "credits_used": 1500},  # 1000~2000 -> 保持
        {"subscription_tier": "ProPlus", "credits_used": 3000},  # >2000 -> 升 Pro Max
    ]
    up, down = recommendations(rows)
    assert [(u["credits_used"], u["suggested_tier"]) for u in up] == [(3000, "ProMax")]
    assert [(d["credits_used"], d["suggested_tier"]) for d in down] == [(900, "Pro")]


def test_multi_tier_jump_and_over_power():
    rows = [
        {"subscription_tier": "Pro", "credits_used": 6000},      # 只有 Power 覆盖 -> 跨多档升 Power
        {"subscription_tier": "Power", "credits_used": 800},     # 仅需 Pro -> 跨多档降 Pro
        {"subscription_tier": "Pro", "credits_used": 50000},     # 超过 Power 额度 -> 仍取 Power（升）
        {"subscription_tier": "Power", "credits_used": 50000},   # 超过 Power 且已在 Power -> 保持
    ]
    up, down = recommendations(rows)
    ups = {u["credits_used"]: u["suggested_tier"] for u in up}
    downs = {d["credits_used"]: d["suggested_tier"] for d in down}
    assert ups == {6000: "Power", 50000: "Power"}
    assert downs == {800: "Pro"}


def test_keep_and_zero_usage_not_recommended():
    rows = [
        {"subscription_tier": "Pro", "credits_used": 500},       # 用量落在 Pro -> 保持
        {"subscription_tier": "ProMax", "credits_used": 0},      # 零用量 -> 不建议
        {"subscription_tier": "Unknown", "credits_used": 3000},  # 未知当前档 -> 不建议
    ]
    up, down = recommendations(rows)
    assert up == [] and down == []


def test_sorting():
    rows = [
        {"subscription_tier": "Pro", "credits_used": 1500},   # 升 Pro+
        {"subscription_tier": "Pro", "credits_used": 6000},   # 升 Power（用量最高，排最前）
        {"subscription_tier": "Power", "credits_used": 800},  # 降 Pro
        {"subscription_tier": "Power", "credits_used": 300},  # 降 Pro（用量最低，排最前）
    ]
    up, down = recommendations(rows)
    assert [u["credits_used"] for u in up] == [6000, 1500]     # 降序
    assert [d["credits_used"] for d in down] == [300, 800]     # 升序
