"""ETL Lambda handler：读 raw CSV -> transform -> 写 curated Parquet。

触发方式：
- EventBridge 定时（每日）：处理 event 指定日期或"昨天"的全部 client CSV。
- 手动 invoke：event 可含 {"date": "YYYY-MM-DD"} 或 {"backfill": true} 处理全部。

环境变量：
  RAW_BUCKET, RAW_PREFIX       Kiro CSV 所在（mock 模式下即上传的 mock 数据）
  CURATED_BUCKET               Parquet 输出桶
  DEPT_TABLE                   DynamoDB user_department 表名
  CREDIT_UNIT_PRICE            预估成本单价
  MOCK_MODE                    "true" 时行为不变（数据源仍是 RAW_BUCKET）
"""
from __future__ import annotations

import io
import os
from datetime import date, timedelta

import boto3
import pandas as pd

import transform  # 同层打包

s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb")

RAW_BUCKET = os.environ["RAW_BUCKET"]
RAW_PREFIX = os.environ.get("RAW_PREFIX", "").strip("/")
CURATED_BUCKET = os.environ["CURATED_BUCKET"]
DEPT_TABLE = os.environ["DEPT_TABLE"]
CREDIT_UNIT_PRICE = float(os.environ.get("CREDIT_UNIT_PRICE", "0.15"))


def _load_department_map() -> dict[str, dict]:
    table = ddb.Table(DEPT_TABLE)
    items, resp = [], table.scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return {
        it["user_key"]: {
            "department": it.get("department", "未分组"),
            "display_name": it.get("display_name", ""),
            "groups": it.get("groups", []),
        }
        for it in items
    }


def _list_csv_keys(target_day: date | None) -> list[str]:
    """列出 raw 桶中匹配日期的 CSV key。target_day=None 表示全部。"""
    keys, token = [], None
    prefix = RAW_PREFIX + "/" if RAW_PREFIX else ""
    while True:
        kw = {"Bucket": RAW_BUCKET, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)
        for obj in resp.get("Contents", []):
            k = obj["Key"]
            if not k.endswith(".csv"):
                continue
            if target_day is None:
                keys.append(k)
            else:
                seg = f"/{target_day:%Y}/{target_day:%m}/{target_day:%d}/"
                if seg in k:
                    keys.append(k)
        if resp.get("IsTruncated"):
            token = resp["NextContinuationToken"]
        else:
            break
    return keys


def _read_key(key: str) -> pd.DataFrame:
    body = s3.get_object(Bucket=RAW_BUCKET, Key=key)["Body"].read()
    return transform._read_csv(io.BytesIO(body))


def _write_parquet(df: pd.DataFrame, table_name: str) -> int:
    """按 year/month/day 分区写 Parquet 到 curated 桶。"""
    if df.empty:
        return 0
    written = 0
    df = df.copy()
    df["_d"] = pd.to_datetime(df["date"])
    for (y, m, d), part in df.groupby([df["_d"].dt.year, df["_d"].dt.month, df["_d"].dt.day]):
        part = part.drop(columns=["_d"])
        buf = io.BytesIO()
        part.to_parquet(buf, index=False, engine="pyarrow")
        buf.seek(0)
        key = f"{table_name}/year={y}/month={m:02d}/day={d:02d}/{table_name}_{y}{m:02d}{d:02d}.parquet"
        s3.put_object(Bucket=CURATED_BUCKET, Key=key, Body=buf.getvalue())
        written += len(part)
    return written


def handler(event, context):
    event = event or {}
    if event.get("backfill"):
        target = None
    elif event.get("date"):
        target = date.fromisoformat(event["date"])
    else:
        target = date.today() - timedelta(days=1)  # 默认处理昨天

    keys = _list_csv_keys(target)
    if not keys:
        return {"status": "no_data", "target": str(target), "keys": 0}

    dept_map = _load_department_map()
    frames = [_read_key(k) for k in keys]
    fud, fmd = transform.transform(frames, dept_map, CREDIT_UNIT_PRICE)

    n1 = _write_parquet(fud, "fact_user_daily")
    n2 = _write_parquet(fmd, "fact_user_model_daily")
    return {
        "status": "ok",
        "target": str(target),
        "csv_keys": len(keys),
        "fact_user_daily_rows": n1,
        "fact_user_model_daily_rows": n2,
    }
