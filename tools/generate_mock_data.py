#!/usr/bin/env python3
"""生成符合 Kiro 企业版 per-user CSV 真实 schema 的 mock 数据。

参考: https://kiro.dev/docs/enterprise/monitor-and-track/user-activity/

输出:
  mock/AWSLogs/<accountId>/KiroLogs/user_report/<region>/<yyyy>/<mm>/<dd>/00/
        <clientType>_<accountId>_user_report_<ts>.csv
  mock/identity_groups.json   (email -> {department, groups, display_name})

用法:
  python tools/generate_mock_data.py --days 7 --out mock \
      --account-id 123456789012 --region us-west-2
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
from datetime import date, datetime, timedelta, timezone

# --- 固定枚举 -------------------------------------------------------------
CLIENT_TYPES = ["KIRO_IDE", "KIRO_CLI", "PLUGIN"]
TIERS = ["Pro", "ProPlus", "ProMax", "Power"]
# 各档位套餐 credit 上限（Overage 关闭时 Overage_Cap 取此值）
TIER_CAP = {"Pro": 100.0, "ProPlus": 150.0, "ProMax": 200.0, "Power": 400.0}
# 模型列（小写，Auto 在前，其余按字母序）——真实文件动态，这里固定一组便于验收
MODELS = ["auto", "claude_haiku_4_5", "claude_opus_4_1", "claude_sonnet_4_5", "gpt_5", "gpt_5_5"]

# 部门（对应 Identity Center group displayName）—— 可按真实组织替换
DEPARTMENTS = [
    "阳光电源-研发中心",
    "阳光电源-电力电子研究院",
    "阳光数字化中心-软件部",
    "阳光数字化中心-数据部",
    "阳光储能-系统部",
    "未分组",
]
# 各部门权重（人数占比），未分组占少量
DEPT_WEIGHTS = [28, 18, 22, 12, 15, 5]

# 静态列顺序（严格按文档）
STATIC_COLS = [
    "Date", "UserId", "Client_Type", "Chat_Conversations", "Credits_Used",
    "Overage_Cap", "Overage_Credits_Used", "Overage_Enabled", "ProfileId",
    "Subscription_Tier", "Total_Messages", "New_User", "User_Email",
]
MODEL_COLS = [f"{m}_messages" for m in MODELS]
ALL_COLS = STATIC_COLS + MODEL_COLS


def make_users(n: int) -> list[dict]:
    """构造 n 个稳定的用户档案。"""
    first = ["伟", "芳", "娜", "敏", "静", "磊", "军", "洋", "勇", "艳",
             "杰", "涛", "明", "超", "秀兰", "霞", "平", "刚", "桂英", "鹏",
             "华", "建国", "丽", "强", "雪", "云", "斌", "峰", "波", "辉",
             "婷", "浩", "宇", "晨", "琳", "倩", "凯", "健", "亮", "俊"]
    last = ["薛", "帅", "江", "纽", "严", "张", "王", "吴", "闫", "卢",
            "任", "沈", "陈", "李", "赵", "黄", "周", "郑", "孙", "马",
            "朱", "胡", "林", "郭", "何", "高", "罗", "梁", "宋", "唐"]
    users = []
    for i in range(n):
        name = random.choice(last) + random.choice(first)
        email = f"user{i:03d}@example.com"
        tier = random.choices(TIERS, weights=[10, 5, 30, 55])[0]  # 贴近参考 UI 分布
        # 前 3 个用户强制为「未分组」，确保验收覆盖未分组分支
        dept = "未分组" if i < 3 else random.choices(DEPARTMENTS, weights=DEPT_WEIGHTS)[0]
        users.append({
            "user_id": f"idc-user-{i:05d}",
            "email": email,
            "name": name,
            "tier": tier,
            "department": dept,
            "profile_id": "profile-KIRO-PRO",
            "activity": random.random(),  # 0..1，决定活跃度
        })
    return users


def gen_day_rows(users: list[dict], day: date, is_first_day: bool) -> dict[str, list[dict]]:
    """为某一天生成各 client 的行。返回 {client_type: [row,...]}。"""
    out = {c: [] for c in CLIENT_TYPES}
    for u in users:
        # 该用户当天是否活跃
        if random.random() > (0.35 + 0.6 * u["activity"]):
            continue
        # 用户当天可能在 1~2 个 client 上活动
        clients = random.sample(CLIENT_TYPES, k=random.choice([1, 1, 2]))
        for ct in clients:
            cap = TIER_CAP[u["tier"]]
            credits_used = round(random.uniform(0.5, cap * 1.1) * u["activity"], 1)
            overage_enabled = u["tier"] in ("ProMax", "Power") and random.random() < 0.2
            overage_used = 0.0
            if credits_used > cap:
                if overage_enabled:
                    overage_used = round(credits_used - cap, 1)
                else:
                    credits_used = round(min(credits_used, cap), 1)
            convs = int(credits_used / random.uniform(2, 6)) + random.randint(0, 3)
            total_msgs = convs * random.randint(4, 20)
            new_user = is_first_day and random.random() < 0.05

            row = {
                "Date": day.isoformat(),
                "UserId": u["user_id"],
                "Client_Type": ct,
                "Chat_Conversations": convs,
                "Credits_Used": credits_used,
                "Overage_Cap": cap,
                "Overage_Credits_Used": overage_used,
                "Overage_Enabled": str(overage_enabled).lower(),
                "ProfileId": u["profile_id"],
                "Subscription_Tier": u["tier"],
                "Total_Messages": total_msgs,
                "New_User": str(new_user).lower(),
                "User_Email": u["email"],
            }
            # 动态模型列：把 total_msgs 分摊到若干模型
            remaining = total_msgs
            model_msgs = {mc: 0 for mc in MODEL_COLS}
            active_models = random.sample(MODEL_COLS, k=random.randint(1, len(MODEL_COLS)))
            for j, mc in enumerate(active_models):
                if j == len(active_models) - 1:
                    model_msgs[mc] = remaining
                else:
                    take = random.randint(0, remaining)
                    model_msgs[mc] = take
                    remaining -= take
            row.update(model_msgs)
            out[ct].append(row)
    return out


def write_csv(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ALL_COLS)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--users", type=int, default=40)
    ap.add_argument("--out", default="mock")
    ap.add_argument("--account-id", default="123456789012")
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--prefix", default="user-activity-reports")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)
    users = make_users(args.users)

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=args.days - 1)

    total_rows = 0
    for d in range(args.days):
        day = start + timedelta(days=d)
        by_client = gen_day_rows(users, day, is_first_day=(d == 0))
        for ct, rows in by_client.items():
            if not rows:
                continue
            ts = datetime.combine(day, datetime.min.time()).strftime("%Y%m%dT020000Z")
            # 路径贴近真实: prefix/AWSLogs/acct/KiroLogs/user_report/region/yyyy/mm/dd/00/
            rel = os.path.join(
                args.prefix, "AWSLogs", args.account_id, "KiroLogs", "user_report",
                args.region, f"{day:%Y}", f"{day:%m}", f"{day:%d}", "00",
                f"{ct}_{args.account_id}_user_report_{ts}.csv",
            )
            write_csv(rows, os.path.join(args.out, rel))
            total_rows += len(rows)

    # identity_groups.json —— 部分用户故意留空 group（=> 未分组）
    identity = {}
    for u in users:
        if u["department"] == "未分组":
            groups = []
            dept = "未分组"
        else:
            groups = [u["department"]]
            dept = u["department"]
        identity[u["email"]] = {
            "department": dept,
            "groups": groups,
            "display_name": u["name"],
            "user_id": u["user_id"],
        }
    with open(os.path.join(args.out, "identity_groups.json"), "w", encoding="utf-8") as f:
        json.dump(identity, f, ensure_ascii=False, indent=2)

    print(f"生成完成: {total_rows} 行 CSV, {len(users)} 个用户, {args.days} 天")
    print(f"输出目录: {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
