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


def test_est_cost():
    assert dv.est_cost(100, 0.15) == 15.0
    assert dv.est_cost(0, 0.15) == 0.0


def test_est_cost_default_price_is_kiro_official():
    # 默认单价 = Kiro 官方超额单价 $0.04/credit
    assert dv.DEFAULT_CREDIT_UNIT_PRICE == 0.04
    assert dv.est_cost(100) == 4.0


def test_is_overage():
    # 超额 = credits > usage_limit
    assert dv.is_overage(1200, 1000) is True
    assert dv.is_overage(1000, 1000) is False
    assert dv.is_overage(500, 1000) is False
    # 额度为 0/无效时不算超额（避免误报）
    assert dv.is_overage(500, 0) is False
