"""派生字段口径（单一真实来源）。

口径（基于真实 Kiro 数据校准）：
- usage_rate = Credits_Used / Usage_Limit（套餐额度，PRO=1000/PRO_MAX=5000/POWER=10000）
- balance    = Usage_Limit - Credits_Used（下限 0）
- est_cost   = credit_unit_price * Credits_Used（Kiro 官方超额单价 $0.04/credit）

注：早期误用 Overage_Cap（恒=10000），导致使用率/余额/超额都不准；
现改用 Usage_Limit（每用户真实额度）。函数签名保持通用（cap 参数传 usage_limit）。

所有需要派生字段的地方（ETL / API / Agent）都从这里取，避免口径漂移。
"""
from __future__ import annotations

DEFAULT_CREDIT_UNIT_PRICE = 0.04  # Kiro 官方超额/加购 credit 单价


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


def est_cost(credits_used: float, credit_unit_price: float = DEFAULT_CREDIT_UNIT_PRICE) -> float:
    """预估成本 = 单价 * 已用 credits（UI 需标注为估算）。"""
    return round(credits_used * credit_unit_price, 2)
