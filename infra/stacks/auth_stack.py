"""AuthStack：Cognito 管理员认证。

- User Pool：关闭自助注册（self_sign_up_enabled=False），邮箱登录，强密码。
- App Client：OAuth2 授权码 + PKCE（public client，无 secret），回调/登出 URL 变量化。
- Hosted UI 域：Cognito 提供的 <prefix>.auth.<region>.amazoncognito.com。
- `admins` 组：JWT 的 cognito:groups 含 admins 才放行（由 API/Agent 的 JWT authorizer 校验）。

输出 userPoolId / clientId / hostedUiDomain / issuer，供 ApiStack 与前端使用。
"""
from __future__ import annotations

from aws_cdk import (
    Stack, RemovalPolicy, Duration, CfnOutput, CustomResource,
    aws_cognito as cognito,
    aws_lambda as lambda_,
    aws_iam as iam,
    custom_resources as cr,
)
from constructs import Construct


class AuthStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, cfg: dict, **kw):
        super().__init__(scope, cid, **kw)

        # --- 首个管理员邮箱校验（synth 阶段阻止无效部署）---
        admin_email = (cfg.get("adminEmail") or "").strip()
        if admin_email == "" or admin_email.lower() == "admin@example.com":
            raise ValueError(
                "请在 infra/cdk.json 的 context.adminEmail 填写真实的首个管理员邮箱"
                "（部署时将自动创建该管理员并发送邀请邮件）；"
                "当前为空或仍是占位符 admin@example.com，已中止 synth。"
            )

        # 回调 URL：前端 CloudFront 域在 WebStack 之后才有；v1 先放本地 + 占位，
        # 部署 WebStack 后可用 context 覆盖 callbackUrls / logoutUrls。
        callback_urls = _as_list(cfg.get("callbackUrls")) or [
            "http://localhost:5173/callback", "http://localhost:5173/"]
        logout_urls = _as_list(cfg.get("logoutUrls")) or [
            "http://localhost:5173/", "http://localhost:5173/logout"]
        domain_prefix = cfg.get("cognitoDomainPrefix") or f"kirodash-{self.account}"

        # 登录地址（部署 WebStack 拿到 CloudFront 域后回填 context.appUrl；首次部署可能为空）。
        # 用于邀请邮件正文，让首个管理员知道去哪登录。为空时邮件给出"向管理员索取地址"提示。
        app_url = (cfg.get("appUrl") or "").strip()
        _login_line_zh = (f"登录地址：{app_url}" if app_url
                          else "登录地址：请向部署人员索取看板访问地址。")
        _login_line_en = (f"Sign-in URL: {app_url}" if app_url
                          else "Sign-in URL: please ask your deployment admin for the dashboard URL.")
        # 自定义邀请邮件（Cognito 自带邮件）：正文必须含 {username} 与 {####}（临时密码），
        # 其余文本自定义——把登录地址放进来。中英双语写在同一封。
        invite_subject = "Kiro 能效分析助手 账号邀请 / Kiro Efficiency Analytics Assistant invitation"
        invite_body = (
            "您的 Kiro 能效分析助手 管理员账号已创建。<br/>"
            f"{_login_line_zh}<br/>"
            "用户名：{username}<br/>"
            "临时密码：{####}<br/>"
            "首次登录后请立即修改密码。<br/><br/>"
            "Your Kiro Efficiency Analytics Assistant admin account has been created.<br/>"
            f"{_login_line_en}<br/>"
            "Username: {username}<br/>"
            "Temporary password: {####}<br/>"
            "Please change your password on first sign-in."
        )

        # --- User Pool ---
        pool = cognito.UserPool(
            self, "UserPool",
            user_pool_name="kiro-dashboard-admins",
            self_sign_up_enabled=False,                 # R3：关闭自助注册
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=12, require_lowercase=True, require_uppercase=True,
                require_digits=True, require_symbols=True,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            # 自定义邀请邮件：含登录地址 + 用户名 + 临时密码（中英双语）。
            user_invitation=cognito.UserInvitationConfig(
                email_subject=invite_subject,
                email_body=invite_body,
            ),
            removal_policy=RemovalPolicy.DESTROY,        # 测试环境；生产改 RETAIN
        )

        # --- Hosted UI 域 ---
        domain = pool.add_domain(
            "HostedUiDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
        )

        # --- App Client（OAuth2 code + PKCE，public，无 secret）---
        client = pool.add_client(
            "WebClient",
            user_pool_client_name="kiro-dashboard-web",
            generate_secret=False,                       # SPA public client
            auth_flows=cognito.AuthFlow(user_srp=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL,
                        cognito.OAuthScope.PROFILE],
                callback_urls=callback_urls,
                logout_urls=logout_urls,
            ),
            prevent_user_existence_errors=True,
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
            enable_token_revocation=True,
        )

        # --- admins 组（R2）---
        admins_group = cognito.CfnUserPoolGroup(
            self, "AdminsGroup",
            user_pool_id=pool.user_pool_id,
            group_name="admins",
            description="Kiro 看板管理员（可访问全量数据）",
        )

        # --- 首次部署自动创建首个管理员（custom resource）---
        # 幂等：用户已存在时跳过创建、不重发邀请邮件、不改密码；AddUserToGroup 对已在组用户幂等。
        # 邮件走 Cognito 自带（不传 MessageAction），由 Cognito 发含用户名+临时密码的邀请邮件。
        first_admin_fn = lambda_.Function(
            self, "FirstAdminFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            timeout=Duration.minutes(2),
            environment={
                "USER_POOL_ID": pool.user_pool_id,
                "ADMIN_EMAIL": admin_email,
            },
            code=lambda_.Code.from_inline(
                "import os\n"
                "import boto3\n"
                "\n"
                "\n"
                "def handler(event, context):\n"
                "    request_type = event.get('RequestType')\n"
                "    if request_type == 'Delete':\n"
                "        # 不删除管理员账户，避免误删。\n"
                "        return {'PhysicalResourceId': os.environ['ADMIN_EMAIL']}\n"
                "\n"
                "    pool_id = os.environ['USER_POOL_ID']\n"
                "    email = os.environ['ADMIN_EMAIL']\n"
                "    client = boto3.client('cognito-idp')\n"
                "\n"
                "    # 1. 创建管理员用户；不传 MessageAction，Cognito 自动发邀请邮件。\n"
                "    try:\n"
                "        client.admin_create_user(\n"
                "            UserPoolId=pool_id,\n"
                "            Username=email,\n"
                "            UserAttributes=[\n"
                "                {'Name': 'email', 'Value': email},\n"
                "                {'Name': 'email_verified', 'Value': 'true'},\n"
                "            ],\n"
                "            DesiredDeliveryMediums=['EMAIL'],\n"
                "        )\n"
                "    except client.exceptions.UsernameExistsException:\n"
                "        # 已存在：跳过创建、不重发邮件、不改密码（幂等）。\n"
                "        pass\n"
                "\n"
                "    # 2. 加入 admins 组（对已在组用户幂等）。\n"
                "    client.admin_add_user_to_group(\n"
                "        UserPoolId=pool_id,\n"
                "        Username=email,\n"
                "        GroupName='admins',\n"
                "    )\n"
                "    return {'PhysicalResourceId': email}\n"
            ),
        )
        # 最小权限：仅建用户 + 加组，限定到本 User Pool ARN。
        first_admin_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["cognito-idp:AdminCreateUser", "cognito-idp:AdminAddUserToGroup"],
            resources=[pool.user_pool_arn],
        ))

        first_admin_provider = cr.Provider(
            self, "FirstAdminProvider",
            on_event_handler=first_admin_fn,
        )
        first_admin = CustomResource(
            self, "FirstAdmin",
            service_token=first_admin_provider.service_token,
            properties={"AdminEmail": admin_email},
        )
        # custom resource 在 User Pool + admins 组创建之后执行。
        first_admin.node.add_dependency(admins_group)
        first_admin.node.add_dependency(pool)

        # --- 暴露给其它 stack ---
        self.user_pool = pool
        self.user_pool_client = client
        self.issuer = f"https://cognito-idp.{self.region}.amazonaws.com/{pool.user_pool_id}"

        hosted_ui = f"https://{domain_prefix}.auth.{self.region}.amazoncognito.com"
        CfnOutput(self, "UserPoolId", value=pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=client.user_pool_client_id)
        CfnOutput(self, "HostedUiDomain", value=hosted_ui)
        CfnOutput(self, "Issuer", value=self.issuer)


def _as_list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [s.strip() for s in str(v).split(",") if s.strip()]
