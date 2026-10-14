"""identity-sync handler 单测：全程 stub/fake/monkeypatch，绝不触碰真实 AWS。

backend/identity_sync 无 __init__.py，且 handler.py 在 import 时读 os.environ["DEPT_TABLE"]
并建 boto3 client/resource。因此用 fixture 先设必需 env，再用 importlib 按绝对路径加载为模块对象 h。
"""
import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ParamValidationError

_HANDLER_PATH = (
    Path(__file__).resolve().parent.parent / "identity_sync" / "handler.py"
)


@pytest.fixture(scope="module")
def h():
    """按绝对路径加载 identity_sync/handler.py 为独立模块对象。"""
    os.environ.setdefault("DEPT_TABLE", "test-dept-table")
    os.environ.setdefault("IDENTITY_STORE_ID", "d-test")
    os.environ.setdefault("AWS_REGION", "us-west-2")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
    spec = importlib.util.spec_from_file_location("idsync_handler_under_test", _HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _reset_ext_flag(h):
    """每个用例前后把进程级 _ext_unsupported 复位，避免模块级全局状态在用例间泄漏。"""
    h._ext_unsupported = False
    yield
    h._ext_unsupported = False


# ---------- _iso_created_at ----------

def test_iso_created_at_datetime(h):
    dt = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert h._iso_created_at(dt) == dt.isoformat()


def test_iso_created_at_none(h):
    assert h._iso_created_at(None) == ""


def test_iso_created_at_non_datetime_falls_back(h):
    # 非 datetime（无 .isoformat）走窄 try 回退到 str()
    assert h._iso_created_at("2024-01-01") == "2024-01-01"


# ---------- _employee_number_from ----------

def test_employee_number_none(h):
    # orchestrator 确认事实③：无工号时 Extensions=None
    assert h._employee_number_from(None) == ""


def test_employee_number_empty_list(h):
    assert h._employee_number_from([]) == ""


def test_employee_number_list_direct(h):
    exts = [{"employeeNumber": "E100"}]
    assert h._employee_number_from(exts) == "E100"


def test_employee_number_list_nested_enterprise(h):
    exts = [{"enterprise": {"employeeNumber": "E200", "department": "RD"}}]
    assert h._employee_number_from(exts) == "E200"


def test_employee_number_dict_direct(h):
    assert h._employee_number_from({"employeeNumber": "E9"}) == "E9"


def test_employee_number_dict_nested(h):
    assert h._employee_number_from({"enterprise": {"employeeNumber": "E9"}}) == "E9"


def test_employee_number_namespace_prefixed_key(h):
    # 命名空间前缀键名（大小写不敏感、以 employeenumber 结尾）
    exts = [{"urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {
        "EmployeeNumber": "E300"}}]
    assert h._employee_number_from(exts) == "E300"


def test_employee_number_missing_field(h):
    assert h._employee_number_from([{"department": "RD"}]) == ""


def test_employee_number_strips_value(h):
    assert h._employee_number_from([{"employeeNumber": "  E7  "}]) == "E7"


# ---------- _is_param_unsupported ----------

def test_is_param_unsupported_paramvalidation(h):
    err = ParamValidationError(report="Unknown parameter in input: Extensions")
    assert h._is_param_unsupported(err) is True


def test_is_param_unsupported_typeerror(h):
    assert h._is_param_unsupported(TypeError("unexpected keyword argument Extensions")) is True


def test_is_param_unsupported_message(h):
    err = Exception("Unknown parameter in input: \"Extensions\", must be one of ...")
    assert h._is_param_unsupported(err) is True


def test_is_param_unsupported_rejects_access_denied(h):
    err = Exception("AccessDeniedException: not authorized to perform identitystore:DescribeUser")
    assert h._is_param_unsupported(err) is False


# ---------- _describe_user_with_ext ----------

class _FakeIds:
    """fake identitystore client：记录每次 describe_user 调用是否带 Extensions。"""

    def __init__(self, fail_with=None, fail_on_ext=True):
        self.calls = []
        self._fail_with = fail_with
        self._fail_on_ext = fail_on_ext

    def describe_user(self, **kwargs):
        has_ext = "Extensions" in kwargs
        self.calls.append(kwargs)
        if self._fail_with is not None and (has_ext or not self._fail_on_ext):
            raise self._fail_with
        return {"UserId": kwargs["UserId"], "with_ext": has_ext}


def test_describe_user_with_ext_happy_path(h):
    ids = _FakeIds()
    out = h._describe_user_with_ext(ids, "u1")
    assert out["with_ext"] is True
    assert len(ids.calls) == 1
    assert ids.calls[0]["Extensions"] == [h._ENTERPRISE_EXT]
    assert h._ext_unsupported is False


def test_describe_user_with_ext_fallback_on_unsupported(h):
    err = ParamValidationError(report="Unknown parameter in input: Extensions")
    ids = _FakeIds(fail_with=err, fail_on_ext=True)
    out = h._describe_user_with_ext(ids, "u1")
    # 第一次带 Extensions 抛错 -> 回退第二次不带 Extensions 成功
    assert out["with_ext"] is False
    assert len(ids.calls) == 2
    assert "Extensions" in ids.calls[0]
    assert "Extensions" not in ids.calls[1]
    assert h._ext_unsupported is True


def test_describe_user_with_ext_shortcircuits_after_unsupported(h):
    err = ParamValidationError(report="Unknown parameter in input: Extensions")
    ids = _FakeIds(fail_with=err, fail_on_ext=True)
    h._describe_user_with_ext(ids, "u1")  # 触发短路
    ids2 = _FakeIds()
    h._describe_user_with_ext(ids2, "u2")  # 已短路，直接不带 Extensions
    assert len(ids2.calls) == 1
    assert "Extensions" not in ids2.calls[0]


def test_describe_user_with_ext_reraises_access_denied(h):
    err = Exception("AccessDeniedException: not authorized")
    ids = _FakeIds(fail_with=err, fail_on_ext=True)
    with pytest.raises(Exception) as ei:
        h._describe_user_with_ext(ids, "u1")
    assert "AccessDenied" in str(ei.value)
    # 权限类异常不被吞、不触发短路、不回退（只调了一次）
    assert h._ext_unsupported is False
    assert len(ids.calls) == 1


# ---------- _user_email_display 整合 ----------

def test_user_email_display_integration(h):
    created = datetime(2024, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

    class _Ids:
        def describe_user(self, **kwargs):
            return {
                "UserId": kwargs["UserId"],
                "Emails": [{"Value": "User@Example.com", "Primary": True}],
                "DisplayName": "张三",
                "CreatedAt": created,
                "Extensions": [{"enterprise": {"employeeNumber": "E42"}}],
            }

    email, display, created_iso, empno = h._user_email_display(_Ids(), "u1")
    assert email == "user@example.com"
    assert display == "张三"
    assert created_iso == created.isoformat()
    assert empno == "E42"


def test_user_email_display_empty_extensions(h):
    class _Ids:
        def describe_user(self, **kwargs):
            return {
                "Emails": [{"Value": "a@example.com", "Primary": True}],
                "DisplayName": "甲",
                "CreatedAt": None,
                "Extensions": None,
            }

    email, display, created_iso, empno = h._user_email_display(_Ids(), "u1")
    assert email == "a@example.com"
    assert created_iso == ""
    assert empno == ""


# ---------- handler() mock 模式 put_item 捕获 ----------

class _FakeBatchWriter:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def put_item(self, Item):
        self._sink.append(Item)

    def delete_item(self, Key):
        pass


class _FakeTable:
    def __init__(self, sink):
        self._sink = sink

    def batch_writer(self):
        return _FakeBatchWriter(self._sink)

    def scan(self, **kwargs):
        return {"Items": []}


def test_handler_mock_mode_writes_two_columns(h, monkeypatch):
    sink = []
    monkeypatch.setattr(h, "MOCK_MODE", True)
    monkeypatch.setattr(h, "_load_mock", lambda: {
        "a@example.com": {
            "department": "研发", "groups": [], "display_name": "甲",
            "employee_number": "E1", "created_date": "2024-01-01T00:00:00+00:00",
        },
    })

    class _Ddb:
        def Table(self, name):
            return _FakeTable(sink)

    monkeypatch.setattr(h, "ddb", _Ddb())

    out = h.handler({}, None)
    assert out["status"] == "ok"
    assert out["mock_mode"] is True
    assert len(sink) == 1
    item = sink[0]
    assert item["user_key"] == "a@example.com"
    assert item["employee_number"] == "E1"
    assert item["created_date"] == "2024-01-01T00:00:00+00:00"
    # 保留原有列
    assert item["department"] == "研发"
    assert item["groups"] == []
    assert item["display_name"] == "甲"
    assert "synced_at" in item


def test_handler_mock_mode_defaults_missing_columns_to_empty(h, monkeypatch):
    sink = []
    monkeypatch.setattr(h, "MOCK_MODE", True)
    monkeypatch.setattr(h, "_load_mock", lambda: {
        "b@example.com": {"department": "研发", "groups": [], "display_name": "乙"},
    })

    class _Ddb:
        def Table(self, name):
            return _FakeTable(sink)

    monkeypatch.setattr(h, "ddb", _Ddb())

    h.handler({}, None)
    item = sink[0]
    assert item["employee_number"] == ""
    assert item["created_date"] == ""
