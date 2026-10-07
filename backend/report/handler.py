"""月度使用报告 Lambda：月末前 7 天发送当月使用报告 + 调档建议。

触发方式：
- EventBridge 每日触发；handler 内判断"今天是否为当月最后一天 - 6"，命中才发送
  （每月这天到月末正好 7 天窗口）。用 {"force": true} 可绕过日期判断手动触发测试。

报告内容（当月 1 号 → 今天，进行中）：
- 汇总：注册/活跃、激活率、总 credits、预估成本、超额人数、档位分布；
- 调档建议（套餐匹配）：按当月实际用量匹配能覆盖的最便宜档位，
  高于当前档→升级、低于→降级（可跨多档）、相同→保持。

发送：Amazon SES（HTML 邮件 + CSV 附件）。收件人 = REPORT_RECIPIENTS（默认 = 管理员）。

环境变量：
  GLUE_DB, ATHENA_WORKGROUP, DEPLOY_REGION/AWS_REGION, DEPT_TABLE, CREDIT_UNIT_PRICE
  SES_SENDER            已在 SES 验证的发件邮箱（必填才发送）
  REPORT_RECIPIENTS     逗号分隔收件人；为空回退 SES_SENDER
"""
from __future__ import annotations

import csv
import io
import os
from datetime import date, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3

try:
    from shared.athena_client import Queries, QueryLayer, make_athena_runner
except ImportError:  # 平铺打包
    from athena_client import Queries, QueryLayer, make_athena_runner  # type: ignore

GLUE_DB = os.environ.get("GLUE_DB", "kiro_usage")
WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "KiroDash-Data-wg")
REGION = os.environ.get("AWS_REGION") or os.environ.get("DEPLOY_REGION", "us-east-1")
CREDIT_UNIT_PRICE = float(os.environ.get("CREDIT_UNIT_PRICE", "0.04"))
SES_SENDER = os.environ.get("SES_SENDER", "")
REPORT_RECIPIENTS = os.environ.get("REPORT_RECIPIENTS", "")
# 收件人动态来源：Cognito admins 组成员邮箱（onboard 新管理员自动纳入）。
# USER_POOL_ID 为空时按池名查找；REQUIRED_GROUP 默认 admins。
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
USER_POOL_NAME = os.environ.get("USER_POOL_NAME", "kiro-dashboard-admins")
ADMIN_GROUP = os.environ.get("REQUIRED_GROUP", "admins")

# 档位阶梯/建议 —— 共享模块（与报告页 API 共用同一算法）
try:
    from shared.recommendations import TIER_ZH, tier_label, recommendations as _recommendations
except ImportError:  # 平铺打包（容器内 shared 与本模块同级）
    from recommendations import TIER_ZH, tier_label, recommendations as _recommendations  # type: ignore


def _last_day_of_month(d: date) -> date:
    first_next = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_next - timedelta(days=1)


def _is_send_day(today: date) -> bool:
    """当月最后一天 - 6：从这天到月末正好 7 天。"""
    return today == _last_day_of_month(today) - timedelta(days=6)


def _fmt(n) -> str:
    try:
        return f"{float(n):,.1f}"
    except (TypeError, ValueError):
        return str(n)


def _pct(rate) -> str:
    return f"{float(rate) * 100:.1f}%"


def _build_html(period_label: str, date_range: tuple, kpis: dict, tiers: dict,
                upgrades: list[dict], downgrades: list[dict]) -> str:
    df, dt = date_range

    def _rows(items: list[dict]) -> str:
        if not items:
            return '<tr><td colspan="6" style="padding:8px;color:#888;">无</td></tr>'
        out = []
        for u in items:
            out.append(
                "<tr>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee;'>{u.get('display_name') or u.get('email','')}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee;'>{u.get('department') or '-'}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee;'>{tier_label(u.get('subscription_tier',''), True)}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right;'>{_fmt(u.get('credits_used'))} / {_fmt(u.get('usage_limit'))}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right;'>{_pct(u.get('usage_rate'))}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee;font-weight:600;'>{tier_label(u.get('suggested_tier',''), True)}</td>"
                "</tr>"
            )
        return "".join(out)

    th = "style='padding:6px 10px;text-align:left;background:#f5f7fa;border-bottom:2px solid #ddd;'"
    tier_line = " ｜ ".join(f"{tier_label(k, True)}: <b>{v}</b>" for k, v in tiers.items())
    return f"""
<html><body style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1f2937;">
  <h2 style="margin:0 0 4px;">Kiro 使用报告 · {period_label}</h2>
  <p style="color:#6b7280;margin:0 0 16px;">统计区间：{df} ~ {dt}（当月至今）</p>

  <h3 style="margin:16px 0 8px;">汇总</h3>
  <ul style="line-height:1.8;margin:0;">
    <li>注册 / 活跃：<b>{kpis.get('registered_users')}</b> / <b>{kpis.get('active_users')}</b>（激活率 {_pct(kpis.get('activation_rate') or 0)}）</li>
    <li>总 Credits：<b>{_fmt(kpis.get('total_credits'))}</b> ｜ 预估成本：<b>${_fmt(kpis.get('est_cost'))}</b>（$0.04/credit）</li>
    <li>超额用户数：<b>{kpis.get('overage_users')}</b> ｜ 日活（均/峰）：{_fmt(kpis.get('daily_active_avg'))} / {kpis.get('daily_active_peak')}</li>
    <li>档位分布：{tier_line}</li>
  </ul>

  <h3 style="margin:20px 0 8px;color:#b45309;">建议升档（用量超出当前档位额度，共 {len(upgrades)} 人）</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px;">
    <tr><th {th}>姓名</th><th {th}>部门</th><th {th}>当前档位</th><th {th}>用量/额度</th><th {th}>使用率</th><th {th}>建议档位</th></tr>
    {_rows(upgrades)}
  </table>

  <h3 style="margin:20px 0 8px;color:#2563eb;">建议降档（更低档位即可覆盖用量，共 {len(downgrades)} 人）</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px;">
    <tr><th {th}>姓名</th><th {th}>部门</th><th {th}>当前档位</th><th {th}>用量/额度</th><th {th}>使用率</th><th {th}>建议档位</th></tr>
    {_rows(downgrades)}
  </table>

  <p style="color:#9ca3af;font-size:12px;margin-top:24px;">
    说明：按当月实际用量匹配能覆盖该用量的最便宜档位（Pro 1000/$20 · Pro+ 2000/$40 ·
    Pro Max 5000/$100 · Power 10000/$200），高于当前档建议升级、低于则建议降级（可跨多档），
    相同则保持。用量超过 Power 额度仍取 Power。本报告由系统自动生成。
  </p>
</body></html>
"""


def _build_csv(upgrades: list[dict], downgrades: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["建议类型", "姓名", "邮箱", "部门", "当前档位", "累计Credits", "套餐额度", "使用率", "建议档位"])
    for kind, items in [("升档", upgrades), ("降档", downgrades)]:
        for u in items:
            w.writerow([
                kind, u.get("display_name") or "", u.get("email") or "",
                u.get("department") or "", tier_label(u.get("subscription_tier") or ""),
                u.get("credits_used"), u.get("usage_limit"),
                _pct(u.get("usage_rate")), tier_label(u.get("suggested_tier") or ""),
            ])
    return buf.getvalue()


def _resolve_pool_id(cog) -> str:
    """取 User Pool ID：优先 env，否则按池名查找。"""
    if USER_POOL_ID:
        return USER_POOL_ID
    token = None
    while True:
        kw = {"MaxResults": 60}
        if token:
            kw["NextToken"] = token
        resp = cog.list_user_pools(**kw)
        for p in resp.get("UserPools", []):
            if p.get("Name") == USER_POOL_NAME:
                return p["Id"]
        token = resp.get("NextToken")
        if not token:
            break
    return ""


def _admin_emails() -> list[str]:
    """从 Cognito admins 组成员取邮箱（动态收件人）。失败或无结果返回空列表。"""
    try:
        cog = boto3.client("cognito-idp", region_name=REGION)
        pool = _resolve_pool_id(cog)
        if not pool:
            return []
        emails, token = [], None
        while True:
            kw = {"UserPoolId": pool, "GroupName": ADMIN_GROUP, "Limit": 60}
            if token:
                kw["NextToken"] = token
            resp = cog.list_users_in_group(**kw)
            for u in resp.get("Users", []):
                attrs = {a["Name"]: a["Value"] for a in u.get("Attributes", [])}
                em = (attrs.get("email") or "").strip()
                if em:
                    emails.append(em)
            token = resp.get("NextToken")
            if not token:
                break
        return emails
    except Exception:  # noqa: BLE001 —— Cognito 不可用时回退到静态配置
        return []


def _ses_verified(ses, emails: list[str]) -> set[str]:
    """返回 emails 中已在 SES 验证（Success）的邮箱集合（小写）。

    沙箱模式下发件给未验证地址会整封被拒，故发送前过滤掉未验证收件人。
    若已解除沙箱（生产访问），可以发任意地址，但过滤也无害（只要地址已验证就保留）。
    """
    if not emails:
        return set()
    try:
        attrs = ses.get_identity_verification_attributes(
            Identities=list({e for e in emails}))["VerificationAttributes"]
        return {k.lower() for k, v in attrs.items()
                if (v or {}).get("VerificationStatus") == "Success"}
    except Exception:  # noqa: BLE001
        return set()


def _send_email(subject: str, html: str, csv_text: str, csv_name: str) -> dict:
    # 收件人：admins 组邮箱 ∪ 显式 REPORT_RECIPIENTS 覆盖；去重；回退 SES_SENDER。
    dynamic = _admin_emails()
    explicit = [r.strip() for r in (REPORT_RECIPIENTS or "").split(",") if r.strip()]
    seen, recipients = set(), []
    for e in dynamic + explicit:
        key = e.lower()
        if key not in seen:
            seen.add(key)
            recipients.append(e)
    if not recipients and SES_SENDER:
        recipients = [SES_SENDER]
    if not SES_SENDER or not recipients:
        return {"sent": False, "reason": "SES_SENDER 或收件人为空", "recipients": recipients}
    ses = boto3.client("ses", region_name=REGION)
    # 过滤：沙箱下仅发已验证收件人，避免一个未验证地址拖垮整封。
    verified = _ses_verified(ses, recipients + [SES_SENDER])
    eligible = [r for r in recipients if r.lower() in verified]
    skipped = [r for r in recipients if r.lower() not in verified]
    if not eligible:
        return {"sent": False, "reason": "无已验证的收件人（SES 沙箱）",
                "recipients": recipients, "skipped_unverified": skipped}
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = SES_SENDER
    msg["To"] = ", ".join(eligible)
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)
    part = MIMEApplication(csv_text.encode("utf-8-sig"))
    part.add_header("Content-Disposition", "attachment", filename=csv_name)
    part.add_header("Content-Type", "text/csv; charset=utf-8")
    msg.attach(part)
    resp = ses.send_raw_email(Source=SES_SENDER, Destinations=eligible,
                              RawMessage={"Data": msg.as_string()})
    return {"sent": True, "message_id": resp.get("MessageId"),
            "recipients": eligible, "skipped_unverified": skipped}


def handler(event, context):
    event = event or {}
    today = date.today()
    if not event.get("force") and not _is_send_day(today):
        return {"status": "skip", "reason": "not send day", "today": str(today),
                "send_day": str(_last_day_of_month(today) - timedelta(days=6))}

    date_from = today.replace(day=1).isoformat()
    date_to = today.isoformat()
    period_label = today.strftime("%Y-%m")

    runner = make_athena_runner(database=GLUE_DB, workgroup=WORKGROUP, region=REGION)
    layer = QueryLayer(runner, cache_ttl=0)
    q = Queries(db=GLUE_DB)

    summary = layer.one(q.summary(date_from, date_to))
    users = layer.run(q.report_users(date_from, date_to))

    # 注册总数（激活率分母）：扫描部门表
    registered = _registered_total()
    total_users = int(summary.get("total_users") or 0)
    active = int(summary.get("active_users") or 0)
    denom = registered or total_users
    kpis = {
        "registered_users": denom,
        "active_users": active,
        "activation_rate": round(active / denom, 4) if denom else 0.0,
        "total_credits": summary.get("total_credits") or 0,
        "est_cost": summary.get("est_cost") or 0,
        "overage_users": int(summary.get("overage_users") or 0),
        "daily_active_avg": summary.get("daily_active_avg") or 0,
        "daily_active_peak": int(summary.get("daily_active_peak") or 0),
    }
    tiers = {
        "Pro": int(summary.get("tier_pro") or 0),
        "ProPlus": int(summary.get("tier_proplus") or 0),
        "ProMax": int(summary.get("tier_promax") or 0),
        "Power": int(summary.get("tier_power") or 0),
    }

    upgrades, downgrades = _recommendations(users)
    html = _build_html(period_label, (date_from, date_to), kpis, tiers, upgrades, downgrades)
    csv_text = _build_csv(upgrades, downgrades)
    subject = f"[Kiro 使用报告] {period_label} · 升档 {len(upgrades)} / 降档 {len(downgrades)}"
    try:
        send_res = _send_email(subject, html, csv_text, f"kiro-adjust-{period_label}.csv")
    except Exception as e:  # noqa: BLE001 —— 发送失败不应让整个任务报错退出
        send_res = {"sent": False, "error": str(e)}

    return {
        "status": "ok", "period": period_label, "range": [date_from, date_to],
        "upgrade_count": len(upgrades), "downgrade_count": len(downgrades),
        "email": send_res,
    }


def _registered_total() -> int:
    table = os.environ.get("DEPT_TABLE", "")
    if not table:
        return 0
    try:
        ddb = boto3.client("dynamodb", region_name=REGION)
        count, kw = 0, {"TableName": table, "Select": "COUNT"}
        while True:
            resp = ddb.scan(**kw)
            count += int(resp.get("Count") or 0)
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kw["ExclusiveStartKey"] = lek
        return count
    except Exception:  # noqa: BLE001
        return 0
