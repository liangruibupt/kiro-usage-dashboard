"""月报 CSV 附件（report/handler.py::_build_csv）单测：CSV 公式注入防护。

report/handler.py 按 Lambda 平铺方式 `from shared.athena_client import ...`，
故把 backend/ 临时加入 sys.path，再用 importlib 按绝对路径加载为独立模块对象。
"""
import csv
import importlib.util
import io
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
_HANDLER_PATH = _BACKEND / "report" / "handler.py"


@pytest.fixture(scope="module")
def rh():
    added = str(_BACKEND) not in sys.path
    if added:
        sys.path.insert(0, str(_BACKEND))
    try:
        spec = importlib.util.spec_from_file_location("report_handler_under_test", _HANDLER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if added:
            sys.path.remove(str(_BACKEND))
    return module


def test_build_csv_neutralizes_formula_injection(rh):
    up = [{"display_name": '=HYPERLINK("http://evil.example","x")', "email": "a@example.com",
           "department": "+cmd", "subscription_tier": "Pro", "credits_used": 1500.0,
           "usage_limit": 1000.0, "usage_rate": 1.5, "suggested_tier": "ProPlus"}]
    down = [{"display_name": "张三", "email": "b@example.com", "department": "研发中心",
             "subscription_tier": "ProMax", "credits_used": 500.0, "usage_limit": 5000.0,
             "usage_rate": 0.1, "suggested_tier": "Pro"}]
    rows = list(csv.reader(io.StringIO(rh._build_csv(up, down))))
    assert rows[0][0] == "建议类型"  # 表头不变
    assert rows[1][1] == '\'=HYPERLINK("http://evil.example","x")'
    assert rows[1][3] == "'+cmd"
    assert rows[1][5] == "1500.0"  # 数值列原样
    assert rows[2][1] == "张三" and rows[2][3] == "研发中心"  # 普通值不变


def test_csv_safe(rh):
    assert rh._csv_safe(None) == ""
    assert rh._csv_safe("-1") == "'-1"
    assert rh._csv_safe("@x") == "'@x"
    assert rh._csv_safe("\tx") == "'\tx"
    assert rh._csv_safe("ok") == "ok"
