"""derivations 口径单测（R19–R21）。"""
from backend.shared import derivations as dv


def test_usage_rate_basic():
    assert dv.usage_rate(50, 100) == 0.5
    assert dv.usage_rate(0, 100) == 0.0


def test_usage_rate_zero_cap():
    # 上限为 0 或负数时返回 0，避免除零
    assert dv.usage_rate(50, 0) == 0.0
    assert dv.usage_rate(50, -1) == 0.0


def test_balance_floor_zero():
    assert dv.balance(30, 100) == 70.0
    # 超额时不小于 0
    assert dv.balance(150, 100) == 0.0


def test_overage_cost():
    # overage 成本 = 超额 credits * 单价（未开 overage 时输入 0 -> 0）
    assert dv.overage_cost(100, 0.04) == 4.0
    assert dv.overage_cost(0) == 0.0
    assert dv.overage_cost(0, 0.04) == 0.0


def test_overage_cost_default_price_is_kiro_official():
    # 默认单价 = Kiro 官方超额单价 $0.04/credit
    assert dv.DEFAULT_CREDIT_UNIT_PRICE == 0.04
    assert dv.overage_cost(100) == 4.0


def test_tier_monthly_cost():
    # 月费按档位（单一真实来源，与前端 TIER_PRICE_USD 对齐）
    assert dv.tier_monthly_cost("Pro") == 20.0
    assert dv.tier_monthly_cost("ProPlus") == 40.0
    assert dv.tier_monthly_cost("ProMax") == 100.0
    assert dv.tier_monthly_cost("Power") == 200.0
    # 未知/空档位返回 0（不臆造）
    assert dv.tier_monthly_cost("") == 0.0
    assert dv.tier_monthly_cost("Unknown") == 0.0


def test_is_overage():
    # 超额 = credits > usage_limit
    assert dv.is_overage(1200, 1000) is True
    assert dv.is_overage(1000, 1000) is False
    assert dv.is_overage(500, 1000) is False
    # 额度为 0/无效时不算超额（避免误报）
    assert dv.is_overage(500, 0) is False
