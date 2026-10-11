"""ApiStack：看板 REST API（API Gateway HTTP API + Lambda(FastAPI/Mangum)）。

- Lambda：平铺打包 backend/api/* + backend/shared/*（handler.py 用相对 import 回退）。
  依赖（fastapi/mangum/pydantic/...）走本地构建的 Lambda layer（无 Docker，见 build.py）。
- HTTP API：Cognito JWT authorizer（校验 issuer/audience）；组级校验（admins）在应用层做
  —— HTTP API 的 JWT authorizer 无法直接断言 cognito:groups，故 Lambda 内再校验组声明。
- 数据面权限：Athena 查询 + 读 curated 桶 + 读 Athena 结果桶 + Glue 元数据。

依赖 DataStack（curated 桶 / workgroup / glue db）与 AuthStack（user pool / client）。
"""
from __future__ import annotations

from aws_cdk import (
    Stack, Duration, CfnOutput,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_apigatewayv2_authorizers as authorizers,
)
from constructs import Construct


class ApiStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, cfg: dict,
                 data_stack, auth_stack, **kw):
        super().__init__(scope, cid, **kw)

        credit_price = str(cfg.get("creditUnitPrice", "0.04"))
        top_n = str(cfg.get("topN", "15"))
        agent_runtime_arn = str(cfg.get("agentRuntimeArn", "") or "")
        ses_sender = (cfg.get("sesSender", "") or "").strip()  # 复用 report 的发件人口径

        curated = data_stack.curated_bucket
        results_bucket_name = data_stack.results_bucket.bucket_name
        workgroup = data_stack.workgroup_name
        glue_db = data_stack.glue_db

        # --- 依赖 layer（本地构建，无 Docker）---
        from build import build_api_assets  # infra/build.py
        deps_layer_dir, app_code_dir = build_api_assets()

        deps_layer = lambda_.LayerVersion(
            self, "ApiDepsLayer",
            code=lambda_.Code.from_asset(deps_layer_dir),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            compatible_architectures=[lambda_.Architecture.X86_64],
            description="fastapi + mangum + pydantic for dashboard API")

        api_fn = lambda_.Function(
            self, "ApiFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            timeout=Duration.seconds(60), memory_size=512,
            layers=[deps_layer],
            code=lambda_.Code.from_asset(app_code_dir),
            environment={
                "GLUE_DB": glue_db,
                "ATHENA_WORKGROUP": workgroup,
                "CREDIT_UNIT_PRICE": credit_price,
                "TOP_N": top_n,
                "DEPT_TABLE": data_stack.dept_table.table_name,
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                "ENFORCE_IDENTITY_FILTER": "false",   # v1 管理员全量
                "REQUIRED_GROUP": "admins",
                "AGENT_RUNTIME_ARN": agent_runtime_arn,
                # 调档申请（Tier_Request · R57/R64/R70）
                "TIER_REQUESTS_TABLE": data_stack.tier_table.table_name,
                "SES_SENDER": ses_sender,
                # 模型倍率维护（ModelMultipliers · FEAT-001）
                "MODEL_MULTIPLIER_TABLE": data_stack.multiplier_table.table_name,
                # 共享 API key：部署期从 Secrets Manager 解析其值并作为 env 注入；
                # 运行时不再调用 Secrets Manager（简化冷启动，密钥轮换 = 重部署）。
                "TIER_API_KEY": data_stack.tier_api_secret.secret_value.unsafe_unwrap(),
            },
        )

        # --- 数据面 IAM（最小权限）---
        api_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["athena:StartQueryExecution", "athena:GetQueryExecution",
                     "athena:GetQueryResults", "athena:StopQueryExecution",
                     "athena:GetWorkGroup"],
            resources=[f"arn:aws:athena:{self.region}:{self.account}:workgroup/{workgroup}"]))
        api_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["glue:GetTable", "glue:GetTables", "glue:GetDatabase",
                     "glue:GetDatabases", "glue:GetPartition", "glue:GetPartitions"],
            resources=[
                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                f"arn:aws:glue:{self.region}:{self.account}:database/{glue_db}",
                f"arn:aws:glue:{self.region}:{self.account}:table/{glue_db}/*",
            ]))
        curated.grant_read(api_fn)
        # 注册用户总数（激活率分母）：扫描部门表计数。
        data_stack.dept_table.grant_read_data(api_fn)
        # 调档申请（Tier_Request · R64）：读写限定到该表 ARN（含 GSI/流）。
        data_stack.tier_table.grant_read_write_data(api_fn)
        # 模型倍率维护（ModelMultipliers · FEAT-001）：读写限定到该表 ARN。
        data_stack.multiplier_table.grant_read_write_data(api_fn)
        # 调档通知邮件（R70）：SES 无细粒度资源级 ARN，故 resources=["*"]。
        api_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:SendRawEmail", "ses:SendEmail"],
            resources=["*"]))
        # 管理员管理（/api/admin/*）：Cognito 管理权限，限定到本 User Pool ARN。
        api_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["cognito-idp:ListUsersInGroup", "cognito-idp:ListUsers",
                     "cognito-idp:AdminCreateUser", "cognito-idp:AdminAddUserToGroup",
                     "cognito-idp:AdminRemoveUserFromGroup", "cognito-idp:AdminResetUserPassword",
                     "cognito-idp:AdminGetUser"],
            resources=[auth_stack.user_pool.user_pool_arn]))
        # AgentCore Runtime 调用（/api/chat 转发 NL 问答）
        if agent_runtime_arn:
            api_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[agent_runtime_arn, agent_runtime_arn + "/*"]))
        # Athena 结果读写（查询结果桶）
        api_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject", "s3:ListBucket",
                     "s3:GetBucketLocation"],
            resources=[
                f"arn:aws:s3:::{results_bucket_name}",
                f"arn:aws:s3:::{results_bucket_name}/*",
            ]))

        # --- HTTP API + JWT authorizer ---
        authorizer = authorizers.HttpJwtAuthorizer(
            "CognitoJwtAuthorizer",
            jwt_issuer=auth_stack.issuer,
            identity_source=["$request.header.Authorization"],
            jwt_audience=[auth_stack.user_pool_client.user_pool_client_id],
        )

        http_api = apigw.HttpApi(
            self, "HttpApi",
            api_name="kiro-dashboard-api",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_headers=["Authorization", "Content-Type"],
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST,
                               apigw.CorsHttpMethod.PUT, apigw.CorsHttpMethod.OPTIONS],
                allow_origins=_as_list(cfg.get("corsOrigins")) or ["*"],
                # 让浏览器能读到 CSV 导出的文件名；HTTP API 的 CORS 由网关生成，
                # 必须在这里暴露，否则前端 fetch 读不到 Content-Disposition（回退默认名）。
                expose_headers=["Content-Disposition"],
            ),
        )

        integration = integrations.HttpLambdaIntegration("ApiIntegration", api_fn)

        # 受保护路由：/api/* 需 JWT（GET + POST + PUT；POST 用于 /api/chat，
        # PUT 用于 /api/model-multipliers 倍率编辑）
        http_api.add_routes(
            path="/api/{proxy+}",
            methods=[apigw.HttpMethod.GET, apigw.HttpMethod.POST, apigw.HttpMethod.PUT],
            integration=integration,
            authorizer=authorizer,
        )
        # health 允许匿名（探活）
        http_api.add_routes(
            path="/api/health",
            methods=[apigw.HttpMethod.GET],
            integration=integration,
        )

        # 调档申请提交端点（R56/R57）：**仅** POST /api/tier-requests 为匿名路由（不挂 JWT
        # authorizer）。HTTP API 精确路由优先于 /api/{proxy+}，故 GET /api/tier-requests
        # （列表）与 .../{id}/close 仍落到上面受 JWT 保护的 proxy 路由。
        tier_routes = http_api.add_routes(
            path="/api/tier-requests",
            methods=[apigw.HttpMethod.POST],
            integration=integration,   # 无 authorizer -> 公开提交
        )
        # 路由级 throttle（R57）：对匿名提交端点单独限流，缓解公网滥用。HTTP API 的自动部署
        # 阶段为 "$default"；在其底层 CfnStage 上按路由键 "POST /api/tier-requests" 设置
        # RouteSettings（burst=5, rate=2 req/s）。
        # 两处坑，均在此处理：
        #  1) CloudFormation 的 AWS::ApiGatewayV2::Stage.RouteSettings 期望 PascalCase 键
        #     （ThrottlingBurstLimit/ThrottlingRateLimit）；若把 L2 RouteSettingsProperty 直接
        #     赋给 cfn_stage.route_settings，CDK 序列化成 lowerCamel 会触发 "Unrecognized field"。
        #     故用 add_override 写入原样 PascalCase JSON。
        #  2) API Gateway 校验 RouteSettings 的键必须指向**已存在**的路由；$default 阶段是
        #     自动部署阶段，CFN 默认不保证先建路由再更新阶段，导致 "Unable to find Route by
        #     key" 404。故显式让 CfnStage 依赖该路由的底层 CfnRoute，强制创建顺序。
        cfn_stage = http_api.default_stage.node.default_child  # type: ignore[union-attr]
        cfn_stage.add_override(
            "Properties.RouteSettings.POST /api/tier-requests",
            {"ThrottlingBurstLimit": 5, "ThrottlingRateLimit": 2},
        )
        for _r in tier_routes:
            cfn_stage.add_dependency(_r.node.default_child)  # type: ignore[arg-type]

        # 远程 MCP 端点（Streamable HTTP）：匿名 POST /mcp（不挂 JWT authorizer），由
        # FastAPI 应用层的共享 X-Api-Key 在工具调用时校验。注：HTTP API 路径不允许
        # 以 "/" 结尾，故只注册 /mcp；应用层已配置 /mcp 直接响应（无 307 尾斜杠重定向）。
        mcp_routes = http_api.add_routes(
            path="/mcp",
            methods=[apigw.HttpMethod.POST],
            integration=integration,   # 无 authorizer -> 公开（工具层校 X-Api-Key）
        )
        cfn_stage.add_override(
            "Properties.RouteSettings.POST /mcp",
            {"ThrottlingBurstLimit": 5, "ThrottlingRateLimit": 2},
        )
        for _r in mcp_routes:
            cfn_stage.add_dependency(_r.node.default_child)  # type: ignore[arg-type]

        self.api_endpoint = http_api.api_endpoint
        CfnOutput(self, "ApiEndpoint", value=http_api.api_endpoint)
        CfnOutput(self, "ApiFnName", value=api_fn.function_name)


def _as_list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [s.strip() for s in str(v).split(",") if s.strip()]
