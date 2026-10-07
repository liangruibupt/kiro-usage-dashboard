"""部署 NL 问答 Agent 到 AgentCore Runtime（CLI/boto3 路径，无 CDK alpha 依赖）。

为什么不走 CDK：Python 版 aws-cdk 的 bedrock-agentcore 构造在本项目 aws-cdk-lib==2.170
下不可用（alpha 仅 npm，且版本 2.268 与 lib 2.170 不匹配）。设计文档 §11 已预留
「构造能力不足时回退 L1/CLI」。本脚本幂等地：
  1. 建/复用 IAM 执行角色（Bedrock invoke + Athena + Glue + S3 读）；
  2. create/update AgentCore Runtime（容器镜像 = ECR arm64 v1），配置 Cognito 入站 JWT 鉴权。

用法：
  python backend/agent/deploy.py --image <ecr-uri>:v1 \
    --user-pool-id us-west-2_xxx --client-id xxx --region us-west-2
输出：runtime ARN（写入 stdout，供前端 config / WebStack 使用）。

注：AgentCore Gateway + Cedar Policy 的工具级授权见 README/后续；v1 工具直接在
Runtime 容器内调用共享查询层，入站 JWT 已由 Runtime 平台校验（管理员组在 API 层已有兜底）。
"""
from __future__ import annotations

import argparse
import json
import time

import boto3

ROLE_NAME = "KiroDashAgentRuntimeRole"


def ensure_role(iam, account: str, region: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": account},
                "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:*"},
            },
        }],
    }
    try:
        role = iam.get_role(RoleName=ROLE_NAME)["Role"]
        print(f"[iam] role exists: {role['Arn']}")
    except iam.exceptions.NoSuchEntityException:
        role = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Kiro dashboard NL agent runtime execution role",
        )["Role"]
        print(f"[iam] created role: {role['Arn']}")

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "Bedrock", "Effect": "Allow",
             "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
             "Resource": "*"},
            {"Sid": "Athena", "Effect": "Allow",
             "Action": ["athena:StartQueryExecution", "athena:GetQueryExecution",
                        "athena:GetQueryResults", "athena:StopQueryExecution",
                        "athena:GetWorkGroup"],
             "Resource": "*"},
            {"Sid": "Glue", "Effect": "Allow",
             "Action": ["glue:GetTable", "glue:GetTables", "glue:GetDatabase",
                        "glue:GetDatabases", "glue:GetPartition", "glue:GetPartitions"],
             "Resource": "*"},
            {"Sid": "S3", "Effect": "Allow",
             "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket",
                        "s3:GetBucketLocation"],
             "Resource": "*"},
            {"Sid": "Logs", "Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": "*"},
            {"Sid": "ECR", "Effect": "Allow",
             "Action": ["ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage",
                        "ecr:GetAuthorizationToken"],
             "Resource": "*"},
        ],
    }
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="KiroDashAgentInline",
                        PolicyDocument=json.dumps(policy))
    print("[iam] inline policy applied")
    return role["Arn"]


def find_runtime(ctrl, name: str):
    paginator = ctrl.get_paginator("list_agent_runtimes")
    for page in paginator.paginate():
        for rt in page.get("agentRuntimes", []):
            if rt["agentRuntimeName"] == name:
                return rt
    return None


def deploy(image: str, user_pool_id: str, client_id: str, region: str,
           name: str = "kiro_usage_agent") -> str:
    sess = boto3.Session(region_name=region)
    account = sess.client("sts").get_caller_identity()["Account"]
    iam = sess.client("iam")
    ctrl = sess.client("bedrock-agentcore-control")

    role_arn = ensure_role(iam, account, region)
    # IAM 传播
    time.sleep(8)

    issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    authorizer = {
        "customJWTAuthorizer": {
            "discoveryUrl": f"{issuer}/.well-known/openid-configuration",
            "allowedClients": [client_id],
        }
    }
    artifact = {"containerConfiguration": {"containerUri": image}}
    network = {"networkMode": "PUBLIC"}
    env = {"AWS_REGION": region, "GLUE_DB": "kiro_usage",
           "ATHENA_WORKGROUP": "KiroDash-Data-wg"}

    existing = find_runtime(ctrl, name)
    if existing:
        rid = existing["agentRuntimeId"]
        print(f"[runtime] updating existing {rid}")
        resp = ctrl.update_agent_runtime(
            agentRuntimeId=rid,
            agentRuntimeArtifact=artifact,
            roleArn=role_arn,
            networkConfiguration=network,
            authorizerConfiguration=authorizer,
            environmentVariables=env,
        )
    else:
        print("[runtime] creating new")
        resp = ctrl.create_agent_runtime(
            agentRuntimeName=name,
            description="Kiro 用量看板 NL 问答 Agent",
            agentRuntimeArtifact=artifact,
            roleArn=role_arn,
            networkConfiguration=network,
            authorizerConfiguration=authorizer,
            environmentVariables=env,
        )
    arn = resp["agentRuntimeArn"]
    print(f"[runtime] ARN: {arn}")
    return arn


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True)
    p.add_argument("--user-pool-id", required=True)
    p.add_argument("--client-id", required=True)
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--name", default="kiro_usage_agent")
    a = p.parse_args()
    arn = deploy(a.image, a.user_pool_id, a.client_id, a.region, a.name)
    print(arn)
