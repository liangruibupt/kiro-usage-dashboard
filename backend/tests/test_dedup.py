"""annotate_duplicate_subscriptions 纯函数边界单测（不触 AWS）。

去重口径（见 design.md §2.2 / plan.md 项 9）：
- 分组键 = employee_number.strip()，空串 = 独立订阅不参与分组；
- 非空分组内 created_date 最早为主（is_duplicate_subscription=False, primary_email=""），
  其余为重复（True, primary_email=主订阅 email）；
- 空 created_date 排最后（绝不被选为主）；组内全缺 created_date 按 email 升序定主；
- 返回顺序 == 输入顺序。
"""
from backend.api.main import annotate_duplicate_subscriptions


def _by_email(rows):
    return {r["email"]: r for r in rows}


def test_same_employee_number_earliest_is_primary():
    users = [
        {"email": "a@x.com", "employee_number": "E1", "created_date": "2024-01-01"},
        {"email": "b@x.com", "employee_number": "E1", "created_date": "2024-02-01"},
        {"email": "c@x.com", "employee_number": "E1", "created_date": "2024-03-01"},
    ]
    out = _by_email(annotate_duplicate_subscriptions(users))
    assert out["a@x.com"]["is_duplicate_subscription"] is False
    assert out["a@x.com"]["primary_email"] == ""
    assert out["b@x.com"]["is_duplicate_subscription"] is True
    assert out["b@x.com"]["primary_email"] == "a@x.com"
    assert out["c@x.com"]["is_duplicate_subscription"] is True
    assert out["c@x.com"]["primary_email"] == "a@x.com"


def test_empty_employee_number_all_independent():
    users = [
        {"email": "a@x.com", "employee_number": "", "created_date": "2024-01-01"},
        {"email": "b@x.com", "employee_number": "", "created_date": "2024-02-01"},
    ]
    out = _by_email(annotate_duplicate_subscriptions(users))
    for e in ("a@x.com", "b@x.com"):
        assert out[e]["is_duplicate_subscription"] is False
        assert out[e]["primary_email"] == ""


def test_missing_created_date_sorts_last_never_primary():
    users = [
        {"email": "a@x.com", "employee_number": "E1", "created_date": ""},
        {"email": "b@x.com", "employee_number": "E1", "created_date": "2024-02-01"},
        {"email": "c@x.com", "employee_number": "E1", "created_date": "2024-01-01"},
    ]
    out = _by_email(annotate_duplicate_subscriptions(users))
    # 有值里最早的 c 为主；空 created_date 的 a 必为重复（排最后，绝不当主）。
    assert out["c@x.com"]["is_duplicate_subscription"] is False
    assert out["c@x.com"]["primary_email"] == ""
    assert out["a@x.com"]["is_duplicate_subscription"] is True
    assert out["a@x.com"]["primary_email"] == "c@x.com"
    assert out["b@x.com"]["is_duplicate_subscription"] is True
    assert out["b@x.com"]["primary_email"] == "c@x.com"


def test_all_missing_created_date_primary_by_email():
    users = [
        {"email": "z@x.com", "employee_number": "E1", "created_date": ""},
        {"email": "a@x.com", "employee_number": "E1", "created_date": ""},
    ]
    out = _by_email(annotate_duplicate_subscriptions(users))
    # 全缺 created_date -> 按 email 升序定主：a 为主，z 为重复。
    assert out["a@x.com"]["is_duplicate_subscription"] is False
    assert out["a@x.com"]["primary_email"] == ""
    assert out["z@x.com"]["is_duplicate_subscription"] is True
    assert out["z@x.com"]["primary_email"] == "a@x.com"


def test_single_row_group_is_primary():
    users = [{"email": "a@x.com", "employee_number": "E9", "created_date": "2024-01-01"}]
    out = _by_email(annotate_duplicate_subscriptions(users))
    assert out["a@x.com"]["is_duplicate_subscription"] is False
    assert out["a@x.com"]["primary_email"] == ""


def test_input_order_preserved():
    users = [
        {"email": "c@x.com", "employee_number": "E1", "created_date": "2024-03-01"},
        {"email": "a@x.com", "employee_number": "E1", "created_date": "2024-01-01"},
        {"email": "x@x.com", "employee_number": "", "created_date": "2024-02-01"},
        {"email": "b@x.com", "employee_number": "E1", "created_date": "2024-02-01"},
    ]
    out = annotate_duplicate_subscriptions(users)
    assert [r["email"] for r in out] == ["c@x.com", "a@x.com", "x@x.com", "b@x.com"]
    # 不原地改：原始 dict 不含去重键
    assert "is_duplicate_subscription" not in users[0]


def test_non_string_inputs_do_not_raise():
    users = [
        {"email": "a@x.com", "employee_number": 1, "created_date": None},
        {"email": "b@x.com", "employee_number": 1, "created_date": "2024-01-01"},
    ]
    out = _by_email(annotate_duplicate_subscriptions(users))
    # employee_number=1(int) -> "1" 分组；b 有 created_date 为主，a(None->"") 排最后为重复。
    assert out["b@x.com"]["is_duplicate_subscription"] is False
    assert out["a@x.com"]["is_duplicate_subscription"] is True
    assert out["a@x.com"]["primary_email"] == "b@x.com"
