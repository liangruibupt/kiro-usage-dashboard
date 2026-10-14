"""部署 NL 问答 Agent 到 AgentCore Runtime（CLI/boto3 路径，无 CDK alpha 依赖）。

为什么不走 CDK：Python 版 aws-cdk 的 bedrock-agentcore 构造在本项目 aws-cdk-lib==2.170
下不可用（alpha 仅 npm，且版本 2.268 与 lib 2.170 不匹配）。设计文档 §11 已预留
「构造能力不足时回退 L1/CLI」。本脚本幂等地：
  1. 建/复用 IAM 执行角色（Bedrock invoke + Athena + Glue + S3 读）；资源 ARN 从
     KiroDash-Data 栈输出与 Athena workgroup 配置解析（需先部署 Data 栈）；
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
import os
import time

import boto3

ROLE_NAME = "KiroDashAgentRuntimeRole"
DATA_STACK = "KiroDash-Data"
# 与 backend/agent/agent.py 的 MODEL_ID 默认值保持一致（跨区域推理配置 ID）。
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
# 跨区域推理配置 ID 的地域前缀（us./eu./apac./global. ...），去掉后即底层基础模型 ID。
_PROFILE_PREFIXES = ("us.", "eu.", "apac.", "global.", "jp.", "au.", "ca.", "us-gov.")


def bedrock_model_arns(model_id: str, account: str, region: str) -> list[str]:
    """Agent 调用的 Bedrock 资源 ARN。

    跨区域推理配置会把请求路由到其它区域的基础模型，故需同时授权 profile ARN
    与各区域的 foundation-model ARN（区域用 *）。
    """
    for p in _PROFILE_PREFIXES:
        if model_id.startswith(p):
            base = model_id[len(p):]
            return [f"arn:aws:bedrock:{region}:{account}:inference-profile/{model_id}",
                    f"arn:aws:bedrock:*::foundation-model/{base}"]
    return [f"arn:aws:bedrock:{region}::foundation-model/{model_id}"]


def resolve_data_resources(sess, stack: str = DATA_STACK) -> dict:
    """从 DataStack 输出 + Athena workgroup 配置解析 workgroup / curated 桶 / 查询结果桶。"""
    outs = sess.client("cloudformation").describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    out = {o["OutputKey"]: o["OutputValue"] for o in outs}
    workgroup = out.get("AthenaWorkgroup") or f"{stack}-wg"
    curated = out["CuratedBucketName"]
    wg = sess.client("athena").get_work_group(WorkGroup=workgroup)["WorkGroup"]
    loc = wg["Configuration"]["ResultConfiguration"]["OutputLocation"]  # s3://bucket/prefix/
    results = loc.split("://", 1)[1].split("/", 1)[0]
    return {"workgroup": workgroup, "curated_bucket": curated, "results_bucket": results}


def build_policy(account: str, region: str, model_id: str, workgroup: str,
                 curated_bucket: str, results_bucket: str) -> dict:
    """Agent Runtime 执行角色的内联策略（按实际使用的资源收敛）。"""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "Bedrock", "Effect": "Allow",
             "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
             "Resource": bedrock_model_arns(model_id, account, region)},
            {"Sid": "Athena", "Effect": "Allow",
             "Action": ["athena:StartQueryExecution", "athena:GetQueryExecution",
                        "athena:GetQueryResults", "athena:StopQueryExecution",
                        "athena:GetWorkGroup"],
             "Resource": f"arn:aws:athena:{region}:{account}:workgroup/{workgroup}"},
            # 有意保留 "*"：Glue 只读目录访问（Athena 查询时读表元数据）。
            {"Sid": "Glue", "Effect": "Allow",
             "Action": ["glue:GetTable", "glue:GetTables", "glue:GetDatabase",
                        "glue:GetDatabases", "glue:GetPartition", "glue:GetPartitions"],
             "Resource": "*"},
            {"Sid": "S3", "Effect": "Allow",
             "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket",
                        "s3:GetBucketLocation"],
             "Resource": [f"arn:aws:s3:::{curated_bucket}", f"arn:aws:s3:::{curated_bucket}/*",
                          f"arn:aws:s3:::{results_bucket}", f"arn:aws:s3:::{results_bucket}/*"]},
            {"Sid": "Logs", "Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/*"},
            # 有意保留 "*"：ecr:GetAuthorizationToken 不支持资源级权限；镜像仓库由 --image 指定。
            {"Sid": "ECR", "Effect": "Allow",
             "Action": ["ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage",
                        "ecr:GetAuthorizationToken"],
             "Resource": "*"},
        ],
    }


def ensure_role(iam, account: str, region: str, policy: dict) -> str:
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

    model_id = os.environ.get("AGENT_MODEL_ID", DEFAULT_MODEL_ID)
    data = resolve_data_resources(sess)
    policy = build_policy(account, region, model_id, data["workgroup"],
                          data["curated_bucket"], data["results_bucket"])
    role_arn = ensure_role(iam, account, region, policy)
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
    # 运行时与执行角色策略使用同一 workgroup / 模型，避免权限与配置不一致。
    env = {"AWS_REGION": region, "GLUE_DB": "kiro_usage",
           "ATHENA_WORKGROUP": data["workgroup"], "AGENT_MODEL_ID": model_id}

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
