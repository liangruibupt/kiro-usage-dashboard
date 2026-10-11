"""看板 API 运行配置（从环境变量注入）。"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    glue_db: str = os.environ.get("GLUE_DB", "kiro_usage")
    athena_workgroup: str = os.environ.get("ATHENA_WORKGROUP", "KiroDash-Data-wg")
    region: str = os.environ.get("AWS_REGION", os.environ.get("DEPLOY_REGION", "us-west-2"))
    credit_unit_price: float = float(os.environ.get("CREDIT_UNIT_PRICE", "0.04"))
    top_n: int = int(os.environ.get("TOP_N", "15"))
    cache_ttl: float = float(os.environ.get("QUERY_CACHE_TTL", "60"))
    # 注册用户总数来源：identity-sync 写入的部门表（含全体已同步用户）。
    # 激活率分母 = 该表条目数（对齐参照看板：注册总数）。
    dept_table: str = os.environ.get("DEPT_TABLE", "")
    # v1 只读管理员全量；预留 v2 强制按身份过滤开关
    enforce_identity_filter: bool = os.environ.get("ENFORCE_IDENTITY_FILTER", "false").lower() == "true"
    # NL 问答 Agent（AgentCore Runtime）ARN；为空则 /api/chat 返回未配置。
    agent_runtime_arn: str = os.environ.get("AGENT_RUNTIME_ARN", "")
    # 管理员管理：Cognito User Pool ID + admins 组名（由 ApiStack 注入）。
    user_pool_id: str = os.environ.get("USER_POOL_ID", "")
    required_group: str = os.environ.get("REQUIRED_GROUP", "admins")
    # 档位申请（tier-requests）：DynamoDB 表名 + 共享 API key + SES 发件人（由 ApiStack 注入）。
    tier_requests_table: str = os.environ.get("TIER_REQUESTS_TABLE", "")
    tier_api_key: str = os.environ.get("TIER_API_KEY", "")        # 由 Secrets Manager/SSM 注入其值
    ses_sender: str = os.environ.get("SES_SENDER", "")            # 复用 report 的发件人口径
    # 模型倍率（credit multiplier）维护：DynamoDB 表名（由 ApiStack 注入）。
    model_multiplier_table: str = os.environ.get("MODEL_MULTIPLIER_TABLE", "")
    # region 复用现有 Settings.region


def get_settings() -> Settings:
    return Settings()
