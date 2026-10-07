"""identity-sync Lambda handler：Identity Center -> DynamoDB user_department。

模式（按 MOCK_MODE 环境变量）：
  MOCK_MODE=true  从 s3://RAW_BUCKET/IDENTITY_JSON_KEY 载入 mock 映射
  否则            从 Identity Center 解析 Kiro 订阅用户 -> 部门映射

【订阅用户来源】Kiro/Q Developer 的订阅用户 = 分配到 Q Developer profile 应用
（SSO application）的 principal。分配可能是：
  - USER：直接把某个用户分配到 profile（如 jiayuanz）——这类用户不经过任何 group；
  - GROUP：把某个组分配到 profile（如 admin 组）——组内成员都是订阅用户。
只遍历 group 会漏掉"直接分配的用户"。因此优先用 SUBSCRIPTION_APP_ARN 读应用分配，
USER 直接纳入、GROUP 展开成员。未配置 ARN 时回退为"遍历全部 group"（旧行为）。

部门口径：用户所属 group 的 DisplayName 作为部门；直接分配（无 group）归"未分组"。

DynamoDB item:
  user_key(PK)=email(小写), department, groups(list), display_name, synced_at
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3

s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb")

DEPT_TABLE = os.environ["DEPT_TABLE"]
MOCK_MODE = os.environ.get("MOCK_MODE", "false").lower() == "true"
RAW_BUCKET = os.environ.get("RAW_BUCKET", "")
IDENTITY_JSON_KEY = os.environ.get("IDENTITY_JSON_KEY", "identity_groups.json")
IDENTITY_STORE_ID = os.environ.get("IDENTITY_STORE_ID", "")
# Identity Store / SSO-admin 是区域性服务，必须调用实例所在区域（可与 Lambda 不同区）。
IDENTITY_STORE_REGION = os.environ.get("IDENTITY_STORE_REGION", "") or os.environ.get("AWS_REGION", "us-west-2")
# SSO 实例 ARN + Q Developer profile 应用 ARN（订阅用户的权威来源）。
SSO_INSTANCE_ARN = os.environ.get("SSO_INSTANCE_ARN", "")
SUBSCRIPTION_APP_ARN = os.environ.get("SUBSCRIPTION_APP_ARN", "")
# Kiro 导出的 user->group 映射 CSV（lookup/user_groups.csv），列：user_email,user_id,group_name,group_id。
# 配置了就优先用它作为部门来源（最权威、最简单，无需 SSO API）。
GROUP_CSV_BUCKET = os.environ.get("GROUP_CSV_BUCKET", "")
GROUP_CSV_KEY = os.environ.get("GROUP_CSV_KEY", "")
UNGROUPED = "未分组"

# 加档组（非部门）：与部门组叠加只影响计费档位，不作为部门归属。
ADDON_GROUP_PREFIXES = ("kiro-power", "kiro-promax", "kiro-proplus", "kiro-pro")


def _is_addon_group(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(n.startswith(p) for p in ADDON_GROUP_PREFIXES)


def _extract_zh_dept(description: str) -> str:
    """从 Identity Center group 的 Description 提取中文部门名。

    真实 Description 形如 '阳光云Kiro Pro subscription users - yangguangyun'，
    中文部门名在开头。规则：取开头连续的中文（含常见中文标点/·-）片段，
    到第一个 ASCII 字母/数字为止。提不出中文则返回空串（调用方回退英文组名）。
    """
    import re
    s = (description or "").strip()
    if not s:
        return ""
    m = re.match(r"^[\u4e00-\u9fff（）()·\-—、／/&＆\s]+", s)
    if not m:
        return ""
    zh = m.group(0).strip(" -—/·、")
    # 至少含一个中文字符才算有效
    return zh if re.search(r"[\u4e00-\u9fff]", zh) else ""


def _load_mock() -> dict[str, dict]:
    body = s3.get_object(Bucket=RAW_BUCKET, Key=IDENTITY_JSON_KEY)["Body"].read()
    return json.loads(body)


def _user_email_display(ids, uid: str) -> tuple[str, str]:
    user = ids.describe_user(IdentityStoreId=IDENTITY_STORE_ID, UserId=uid)
    emails = user.get("Emails", [])
    email = next((e["Value"] for e in emails if e.get("Primary")), None)
    if not email and emails:
        email = emails[0]["Value"]
    display = user.get("DisplayName") or user.get("UserName", "")
    return (email.strip().lower() if email else ""), display


def _group_members(ids, gid: str) -> list[str]:
    uids = []
    mp = ids.get_paginator("list_group_memberships")
    for page in mp.paginate(IdentityStoreId=IDENTITY_STORE_ID, GroupId=gid):
        for m in page.get("GroupMemberships", []):
            uids.append(m["MemberId"]["UserId"])
    return uids


def _add(result: dict, email: str, display: str, group: str | None) -> None:
    if not email:
        return
    entry = result.setdefault(email, {"department": UNGROUPED, "groups": [], "display_name": display})
    if display and not entry.get("display_name"):
        entry["display_name"] = display
    if group:
        if group not in entry["groups"]:
            entry["groups"].append(group)
        # 主部门取第一个 group
        entry["department"] = entry["groups"][0]


def _load_from_subscription_app(ids, sso) -> dict[str, dict]:
    """从 Kiro/Q Developer 订阅 profile 应用的分配解析注册用户（权威名单）。

    订阅用户 = 分配到该应用的 principal：USER 直接纳入；GROUP 展开成员。
    部门：取用户所属【部门组】的中文名（group Description 提取），跳过加档组（kiro-power/promax/proplus）；
    中文名：取 Identity Center 用户 DisplayName。这是注册总数的单一权威来源（对齐订阅席位）。
    """
    result: dict[str, dict] = {}
    assignments = []
    paginator = sso.get_paginator("list_application_assignments")
    for page in paginator.paginate(ApplicationArn=SUBSCRIPTION_APP_ARN):
        assignments.extend(page.get("ApplicationAssignments", []))

    # 先收集每个用户的组归属（组名 + 中文部门名），再定部门。
    for a in assignments:
        pid, ptype = a["PrincipalId"], a["PrincipalType"]
        if ptype == "USER":
            email, display = _user_email_display(ids, pid)
            _add(result, email, display, None)  # 直接分配，无组
        elif ptype == "GROUP":
            grp = ids.describe_group(IdentityStoreId=IDENTITY_STORE_ID, GroupId=pid)
            gname = grp.get("DisplayName", pid)
            zh = _extract_zh_dept(grp.get("Description") or "")
            for uid in _group_members(ids, pid):
                email, display = _user_email_display(ids, uid)
                # 加档组：成员仍计入订阅（录入 result）但不作为部门；
                # 部门组：用中文部门名（提不出中文回退英文组名）。
                if _is_addon_group(gname):
                    _add(result, email, display, None)
                else:
                    _add(result, email, display, zh or gname)
    return result


def _load_from_all_groups(ids) -> dict[str, dict]:
    """回退：遍历全部 group 及其成员（未配置订阅应用 ARN 时用）。"""
    result: dict[str, dict] = {}
    gp = ids.get_paginator("list_groups")
    groups = []
    for page in gp.paginate(IdentityStoreId=IDENTITY_STORE_ID):
        groups.extend(page.get("Groups", []))
    for g in groups:
        gname = g.get("DisplayName", g["GroupId"])
        for uid in _group_members(ids, g["GroupId"]):
            email, display = _user_email_display(ids, uid)
            _add(result, email, display, gname)
    return result


def _load_from_group_csv() -> dict[str, dict]:
    """从 Kiro 导出的 lookup/user_groups.csv 读 user->group 映射。

    列：user_email,user_id,group_name,group_id。一个用户可能属多个组（多行）。

    部门名与用户中文名都从 Identity Center 补全（更权威、通用）：
      - 部门名：取用户所属【部门组】的中文名（来自 group Description 提取），
        跳过 kiro-power/promax/proplus 这类【加档组】；提不出中文回退英文组名。
      - 中文名：取 Identity Center 用户的 DisplayName（回退姓名字段，再回退邮箱前缀）。
    Identity Center 不可用时全部回退（部门=英文组名，中文名=邮箱前缀），不影响主流程。
    """
    import csv
    import io

    body = s3.get_object(Bucket=GROUP_CSV_BUCKET, Key=GROUP_CSV_KEY)["Body"].read()
    text = body.decode("utf-8-sig", errors="replace")
    result: dict[str, dict] = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        email = (row.get("user_email") or "").strip().lower()
        group = (row.get("group_name") or "").strip()
        if not email:
            continue
        _add(result, email, email.split("@")[0], group or None)

    # --- 从 Identity Center 补全中文部门名 + 中文用户名（尽力而为，失败则回退）---
    try:
        ids = boto3.client("identitystore", region_name=IDENTITY_STORE_REGION)
        dept_map = _load_group_dept_map(ids)      # group_name(lower) -> 中文部门名
        name_map = _load_idc_display_names(ids)   # email(lower) -> 中文名
        for email, info in result.items():
            # 部门：取第一个【非加档组】的中文部门名
            dept = UNGROUPED
            for g in info.get("groups", []):
                if _is_addon_group(g):
                    continue
                dept = dept_map.get(g.strip().lower()) or g
                break
            info["department"] = dept
            # 中文名：IdC DisplayName 优先，回退原邮箱前缀
            zh = name_map.get(email)
            if zh:
                info["display_name"] = zh
    except Exception:  # noqa: BLE001 —— IdC 不可用时保留 CSV 回退值
        pass
    return result


def _load_group_dept_map(ids) -> dict[str, str]:
    """Identity Center 所有 group -> 中文部门名映射（key=group DisplayName 小写）。

    中文名从 group Description 提取；加档组跳过；提不出中文的组不入表（回退英文）。
    """
    out: dict[str, str] = {}
    gp = ids.get_paginator("list_groups")
    for page in gp.paginate(IdentityStoreId=IDENTITY_STORE_ID):
        for g in page.get("Groups", []):
            name = (g.get("DisplayName") or "").strip()
            if not name or _is_addon_group(name):
                continue
            zh = _extract_zh_dept(g.get("Description") or "")
            if zh:
                out[name.lower()] = zh
    return out


def _load_idc_display_names(ids) -> dict[str, str]:
    """Identity Center 所有用户 -> 中文 DisplayName 映射（key=主邮箱小写）。

    仅收录 DisplayName 含中文的用户；无中文（拼音/手机号）的不入表，调用方回退邮箱前缀。
    """
    import re
    out: dict[str, str] = {}
    up = ids.get_paginator("list_users")
    for page in up.paginate(IdentityStoreId=IDENTITY_STORE_ID):
        for u in page.get("Users", []):
            emails = u.get("Emails", []) or []
            email = next((e["Value"] for e in emails if e.get("Primary")), None)
            if not email and emails:
                email = emails[0].get("Value")
            if not email:
                continue
            dn = (u.get("DisplayName") or "").strip()
            if not re.search(r"[\u4e00-\u9fff]", dn):
                # 回退：姓名字段拼中文
                fam = (u.get("Name", {}) or {}).get("FamilyName", "") or ""
                giv = (u.get("Name", {}) or {}).get("GivenName", "") or ""
                cand = (fam + giv).strip()
                dn = cand if re.search(r"[\u4e00-\u9fff]", cand) else ""
            if dn:
                out[email.strip().lower()] = dn
    return out


def _load_from_identity_center() -> dict[str, dict]:
    # 优先级：订阅应用分配（权威注册名单）> group CSV > 遍历全部 group。
    # 订阅应用是“注册用户”的单一权威来源（= 实际开通席位，对齐参照看板注册总数）。
    ids = boto3.client("identitystore", region_name=IDENTITY_STORE_REGION)
    if SUBSCRIPTION_APP_ARN:
        sso = boto3.client("sso-admin", region_name=IDENTITY_STORE_REGION)
        return _load_from_subscription_app(ids, sso)
    if GROUP_CSV_BUCKET and GROUP_CSV_KEY:
        return _load_from_group_csv()
    return _load_from_all_groups(ids)


def handler(event, context):
    mapping = _load_mock() if MOCK_MODE else _load_from_identity_center()

    table = ddb.Table(DEPT_TABLE)
    now = datetime.now(timezone.utc).isoformat()
    # 先记录本次同步的 key，稍后清理不再存在的旧用户（避免残留退订用户）。
    current_keys = set()
    written = 0
    with table.batch_writer() as bw:
        for email, info in mapping.items():
            key = email.strip().lower()
            current_keys.add(key)
            bw.put_item(Item={
                "user_key": key,
                "department": info.get("department", UNGROUPED) or UNGROUPED,
                "groups": info.get("groups", []),
                "display_name": info.get("display_name", ""),
                "synced_at": now,
            })
            written += 1

    # 清理不在本次同步结果里的旧条目（真实模式下；mock 模式不清理以免误删）
    removed = 0
    if not MOCK_MODE and current_keys:
        resp = table.scan(ProjectionExpression="user_key")
        stale = [it["user_key"] for it in resp.get("Items", []) if it["user_key"] not in current_keys]
        with table.batch_writer() as bw:
            for k in stale:
                bw.delete_item(Key={"user_key": k})
                removed += 1

    return {"status": "ok", "mock_mode": MOCK_MODE, "users_synced": written, "stale_removed": removed}
