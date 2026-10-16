"""DataStack：数据层基础设施。

- S3: raw（Kiro CSV / mock）、curated（Parquet 长表）、athena-results
- DynamoDB: user_department
- Glue: DB + fact_user_daily / fact_user_model_daily 外部表（partition projection）
- Athena: workgroup
- Lambda: ETL、identity-sync（AWS SDK for pandas layer）
- EventBridge: 每日触发 identity-sync 与 ETL
"""
from __future__ import annotations

import os

from aws_cdk import (
    Stack, RemovalPolicy, Duration, CfnOutput,
    aws_s3 as s3,
    aws_dynamodb as ddb,
    aws_glue as glue,
    aws_athena as athena,
    aws_lambda as lambda_,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))


class DataStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, cfg: dict, **kw):
        super().__init__(scope, cid, **kw)
        mock_mode = str(cfg.get("mockMode", "true")).lower()
        credit_price = str(cfg.get("creditUnitPrice", "0.15"))
        identity_store_id = cfg.get("identityStoreId", "")
        identity_store_region = cfg.get("identityStoreRegion", "") or ""
        sso_instance_arn = cfg.get("ssoInstanceArn", "") or ""
        subscription_app_arn = cfg.get("subscriptionAppArn", "") or ""
        group_csv_bucket = (cfg.get("groupCsvBucket", "") or "").strip()
        group_csv_key = (cfg.get("groupCsvKey", "") or "").strip()
        # 月度报告（SES）：发件人已验证邮箱 + 收件人（逗号分隔，默认回退发件人）。
        ses_sender = (cfg.get("sesSender", "") or "").strip()
        report_recipients = (cfg.get("reportRecipients", "") or cfg.get("adminEmail", "") or "").strip()
        raw_prefix = "user-activity-reports"
        # 真实模式：Kiro 导出的外部源桶（可跨区，如 us-east-1）。
        # 为空时 ETL 读内部 raw 桶（mock）；非空时 ETL 读该外部桶 + 其前缀。
        kiro_source_bucket = (cfg.get("kiroSourceBucket", "") or "").strip()
        kiro_source_prefix = (cfg.get("kiroSourcePrefix", "") or raw_prefix).strip("/")

        # --- S3 ---
        raw = s3.Bucket(self, "RawBucket",
                        removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True,
                        block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                        encryption=s3.BucketEncryption.S3_MANAGED)
        curated = s3.Bucket(self, "CuratedBucket",
                            removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True,
                            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                            encryption=s3.BucketEncryption.S3_MANAGED)
        results = s3.Bucket(self, "AthenaResults",
                            removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True,
                            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                            encryption=s3.BucketEncryption.S3_MANAGED)

        # --- DynamoDB ---
        dept_table = ddb.Table(self, "UserDepartment",
                               partition_key=ddb.Attribute(name="user_key", type=ddb.AttributeType.STRING),
                               billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                               removal_policy=RemovalPolicy.DESTROY)

        # --- Secrets Manager：档位申请公开端点的共享 API key（R57；design §13.8）---
        # 自动生成一个高熵随机值（exclude_punctuation 便于放进 header / MCP 配置）。
        # 部署期由 ApiStack（9.16）用 secret_value.unsafe_unwrap() 解析其值，注入 API
        # Lambda 的 TIER_API_KEY 环境变量；运行时不再调用 Secrets Manager（简化冷启动，
        # 密钥轮换 = 重部署）。运维带外把同一 key 配到各 MCP server 机器。
        tier_api_secret = secretsmanager.Secret(
            self, "TierApiKey",
            secret_name="kiro-dashboard/tier-api-key",
            description="Shared API key protecting POST /api/tier-requests (R57)",
            removal_policy=RemovalPolicy.DESTROY,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=40,
                exclude_punctuation=True,
            ),
        )
        # 调档申请（Tier_Request_Table · R64）：分区键 id（uuid4 hex）。
        tier_table = ddb.Table(self, "TierRequests",
            partition_key=ddb.Attribute(name="id", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY)
        self.tier_table = tier_table

        # 模型倍率维护（ModelMultipliers · FEAT-001）：分区键 model（Athena 真实
        # model 字符串）。倍率种子/编辑由 API 读写。
        multiplier_table = ddb.Table(self, "ModelMultipliers",
            partition_key=ddb.Attribute(name="model", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY)
        self.multiplier_table = multiplier_table

        # --- Glue DB + 外部表（partition projection）---
        gdb = glue.CfnDatabase(self, "GlueDb",
                               catalog_id=self.account,
                               database_input=glue.CfnDatabase.DatabaseInputProperty(name="kiro_usage"))

        def _table(name: str, columns: list[tuple[str, str]]):
            cols = [glue.CfnTable.ColumnProperty(name=n, type=t) for n, t in columns]
            loc = f"s3://{curated.bucket_name}/{name}/"
            # 【性能决策】不使用分区投影。
            # 实测：分区投影（year/month/day）在本数据量下让每次查询多出海量分区枚举开销；
            # 5 个查询并发时单查询 EngineExecutionTime 飙到 ~22s（并发放大），导致 API 503。
            # 改为非分区外部表（Athena 从 LOCATION 递归读取 year=/month=/day= 子目录下的 Parquet），
            # 同样 5 并发查询降到 ~400ms。日期过滤走数据内的 `date` 列（非分区键）。
            # 数据量增大后如需分区裁剪，再引入分区并重新评估投影区间。
            tbl = glue.CfnTable(
                self, f"Tbl{name}",
                catalog_id=self.account,
                database_name="kiro_usage",
                table_input=glue.CfnTable.TableInputProperty(
                    name=name,
                    table_type="EXTERNAL_TABLE",
                    parameters={"classification": "parquet"},
                    storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                        columns=cols,
                        location=loc,
                        input_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                        output_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                        serde_info=glue.CfnTable.SerdeInfoProperty(
                            serialization_library="org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"),
                    ),
                ))
            tbl.add_dependency(gdb)
            return tbl

        _table("fact_user_daily", [
            ("date", "string"), ("user_id", "string"), ("email", "string"),
            ("display_name", "string"), ("department", "string"),
            ("subscription_tier", "string"), ("chat_conversations", "bigint"),
            ("credits_used", "double"), ("overage_cap", "double"),
            ("overage_credits_used", "double"), ("overage_enabled", "boolean"),
            ("total_messages", "bigint"), ("new_user", "boolean"),
            ("usage_rate", "double"), ("balance", "double"),
            ("est_cost", "double"), ("profile_id", "string"),
            ("usage_limit", "double"), ("is_overage", "boolean"),
        ])
        _table("fact_user_model_daily", [
            ("date", "string"), ("email", "string"),
            ("model", "string"), ("model_messages", "bigint"),
        ])

        # --- Athena workgroup ---
        wg = athena.CfnWorkGroup(self, "Workgroup", name=f"{cid}-wg",
                                 recursive_delete_option=True,
                                 work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                                     result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                                         output_location=f"s3://{results.bucket_name}/query-results/")))

        # --- pandas/pyarrow layer（本地构建的 Linux wheel，避免依赖公共 layer 版本号）---
        pandas_layer_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "build", "pandas_layer"))
        pandas_layer = lambda_.LayerVersion(
            self, "PandasLayer",
            code=lambda_.Code.from_asset(pandas_layer_dir),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            compatible_architectures=[lambda_.Architecture.X86_64],
            description="pandas + pyarrow for ETL")

        # ETL 数据源：真实模式指向外部 Kiro 桶，否则内部 raw 桶。
        etl_raw_bucket = kiro_source_bucket or raw.bucket_name
        etl_raw_prefix = kiro_source_prefix if kiro_source_bucket else raw_prefix
        common_env = {
            "RAW_BUCKET": raw.bucket_name,
            "RAW_PREFIX": raw_prefix,
            "CURATED_BUCKET": curated.bucket_name,
            "DEPT_TABLE": dept_table.table_name,
            "CREDIT_UNIT_PRICE": credit_price,
            "MOCK_MODE": mock_mode,
        }

        # ETL Lambda：本地组装 handler + transform + derivations 到构建目录（无需 Docker）
        etl_build = os.path.join(os.path.dirname(__file__), "..", "build", "etl")
        etl_build = os.path.abspath(etl_build)
        os.makedirs(etl_build, exist_ok=True)
        import shutil
        for src in [os.path.join(BACKEND, "etl", "handler.py"),
                    os.path.join(BACKEND, "etl", "transform.py"),
                    os.path.join(BACKEND, "shared", "derivations.py")]:
            shutil.copy2(src, etl_build)

        etl_fn = lambda_.Function(
            self, "EtlFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            timeout=Duration.minutes(5), memory_size=1024,
            layers=[pandas_layer],
            environment={**common_env, "RAW_BUCKET": etl_raw_bucket, "RAW_PREFIX": etl_raw_prefix},
            code=lambda_.Code.from_asset(etl_build),
        )
        raw.grant_read(etl_fn)
        curated.grant_read_write(etl_fn)
        dept_table.grant_read_data(etl_fn)
        # 真实模式：授予 ETL 读外部 Kiro 源桶（跨区 S3 读取无需同区，但需 IAM 授权）。
        if kiro_source_bucket:
            etl_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[f"arn:aws:s3:::{kiro_source_bucket}",
                           f"arn:aws:s3:::{kiro_source_bucket}/*"]))

        # identity-sync Lambda（无需 pandas）
        idsync_fn = lambda_.Function(
            self, "IdentitySyncFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            timeout=Duration.minutes(5), memory_size=256,
            environment={**common_env, "IDENTITY_STORE_ID": identity_store_id,
                         "IDENTITY_STORE_REGION": identity_store_region,
                         "SSO_INSTANCE_ARN": sso_instance_arn,
                         "SUBSCRIPTION_APP_ARN": subscription_app_arn,
                         "GROUP_CSV_BUCKET": group_csv_bucket,
                         "GROUP_CSV_KEY": group_csv_key,
                         "IDENTITY_JSON_KEY": "identity_groups.json"},
            code=lambda_.Code.from_asset(os.path.join(BACKEND, "identity_sync")),
        )
        raw.grant_read(idsync_fn)
        dept_table.grant_read_write_data(idsync_fn)
        # 部门来源 = Kiro 导出的 group CSV：授予 identity-sync 读该桶。
        if group_csv_bucket:
            idsync_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[f"arn:aws:s3:::{group_csv_bucket}",
                           f"arn:aws:s3:::{group_csv_bucket}/*"]))
        # 非 mock 模式：需 Identity Center / SSO 只读权限。
        #  - 订阅应用分配（权威注册名单）：需 SSO ListApplicationAssignments/DescribeApplication + 组成员/描述/用户；
        #  - group CSV / 遍历组：需 组描述（中文部门名）+ 用户 DisplayName。
        # 这些均为只读，统一授予全集（无写操作）。
        if mock_mode != "true":
            idsync_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["identitystore:ListGroups", "identitystore:ListUsers",
                         "identitystore:ListGroupMemberships",
                         "identitystore:DescribeUser", "identitystore:DescribeGroup",
                         "sso:ListApplicationAssignments", "sso:DescribeApplication"],
                resources=["*"]))

        # --- 月度报告 Lambda（SES）：复用共享查询层，无 pandas 依赖 ---
        report_build = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "build", "report"))
        os.makedirs(report_build, exist_ok=True)
        for src in [os.path.join(BACKEND, "report", "handler.py"),
                    os.path.join(BACKEND, "shared", "athena_client.py"),
                    os.path.join(BACKEND, "shared", "recommendations.py"),
                    os.path.join(BACKEND, "shared", "derivations.py")]:
            shutil.copy2(src, report_build)

        report_fn = lambda_.Function(
            self, "MonthlyReportFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            timeout=Duration.minutes(5), memory_size=256,
            environment={
                "GLUE_DB": "kiro_usage",
                "ATHENA_WORKGROUP": wg.name,
                "DEPLOY_REGION": self.region,
                "DEPT_TABLE": dept_table.table_name,
                "CREDIT_UNIT_PRICE": credit_price,
                "SES_SENDER": ses_sender,
                "REPORT_RECIPIENTS": report_recipients,
                # 收件人动态取 Cognito admins 组（按池名查找）；onboard 新管理员自动纳入。
                "USER_POOL_NAME": "kiro-dashboard-admins",
                "REQUIRED_GROUP": "admins",
            },
            code=lambda_.Code.from_asset(report_build),
        )
        # 数据面权限：Athena + Glue + 读 curated + 读写查询结果桶 + 读部门表
        report_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["athena:StartQueryExecution", "athena:GetQueryExecution",
                     "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"],
            resources=[f"arn:aws:athena:{self.region}:{self.account}:workgroup/{wg.name}"]))
        report_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["glue:GetTable", "glue:GetTables", "glue:GetDatabase",
                     "glue:GetDatabases", "glue:GetPartition", "glue:GetPartitions"],
            resources=[
                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                f"arn:aws:glue:{self.region}:{self.account}:database/kiro_usage",
                f"arn:aws:glue:{self.region}:{self.account}:table/kiro_usage/*",
            ]))
        curated.grant_read(report_fn)
        results.grant_read_write(report_fn)
        dept_table.grant_read_data(report_fn)
        # SES 发送：限定到本账户/区域的 SES 已验证身份（发件人/域需已在 SES 验证）
        report_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:SendRawEmail", "ses:SendEmail"],
            resources=[f"arn:aws:ses:{self.region}:{self.account}:identity/*"]))
        # 查验证状态（发送前过滤未验证收件人）：该动作不支持资源级权限，只能 "*"
        report_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:GetIdentityVerificationAttributes"], resources=["*"]))
        # 收件人动态来源：读 Cognito admins 组成员（按池名查找 User Pool）
        report_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["cognito-idp:ListUserPools", "cognito-idp:ListUsersInGroup"],
            resources=["*"]))

        # --- EventBridge 每日：先 identity-sync 后 ETL（此处各自定时，简单起见）---
        events.Rule(self, "DailyIdentitySync",
                    schedule=events.Schedule.cron(minute="30", hour="2"),
                    targets=[targets.LambdaFunction(idsync_fn)])
        events.Rule(self, "DailyEtl",
                    schedule=events.Schedule.cron(minute="45", hour="2"),
                    targets=[targets.LambdaFunction(etl_fn)])
        # 月度报告：每日触发，Lambda 内部判断“当月最后一天 - 6”才发送（月末前 7 天）。
        events.Rule(self, "MonthlyReport",
                    schedule=events.Schedule.cron(minute="0", hour="3"),
                    targets=[targets.LambdaFunction(report_fn)])

        # --- 输出（供其它 stack / 手动验证）---
        self.raw_bucket = raw
        self.curated_bucket = curated
        self.results_bucket = results
        self.dept_table = dept_table
        self.tier_api_secret = tier_api_secret
        self.workgroup_name = wg.name
        self.glue_db = "kiro_usage"
        CfnOutput(self, "RawBucketName", value=raw.bucket_name)
        CfnOutput(self, "CuratedBucketName", value=curated.bucket_name)
        CfnOutput(self, "DeptTableName", value=dept_table.table_name)
        CfnOutput(self, "TierApiKeySecretArn", value=tier_api_secret.secret_arn)
        CfnOutput(self, "TierRequestsTableName", value=tier_table.table_name)
        CfnOutput(self, "ModelMultiplierTableName", value=multiplier_table.table_name)
        CfnOutput(self, "AthenaWorkgroup", value=wg.name)
        CfnOutput(self, "EtlFnName", value=etl_fn.function_name)
        CfnOutput(self, "IdentitySyncFnName", value=idsync_fn.function_name)
        CfnOutput(self, "MonthlyReportFnName", value=report_fn.function_name)
