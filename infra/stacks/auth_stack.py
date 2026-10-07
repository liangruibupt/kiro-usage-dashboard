"""AuthStack：Cognito 管理员认证。

- User Pool：关闭自助注册（self_sign_up_enabled=False），邮箱登录，强密码。
- App Client：OAuth2 授权码 + PKCE（public client，无 secret），回调/登出 URL 变量化。
- Hosted UI 域：Cognito 提供的 <prefix>.auth.<region>.amazoncognito.com。
- `admins` 组：JWT 的 cognito:groups 含 admins 才放行（由 API/Agent 的 JWT authorizer 校验）。

输出 userPoolId / clientId / hostedUiDomain / issuer，供 ApiStack 与前端使用。
"""
from __future__ import annotations

from aws_cdk import (
    Stack, RemovalPolicy, Duration, CfnOutput,
    aws_cognito as cognito,
)
from constructs import Construct


class AuthStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, cfg: dict, **kw):
        super().__init__(scope, cid, **kw)

        # 回调 URL：前端 CloudFront 域在 WebStack 之后才有；v1 先放本地 + 占位，
        # 部署 WebStack 后可用 context 覆盖 callbackUrls / logoutUrls。
        callback_urls = _as_list(cfg.get("callbackUrls")) or [
            "http://localhost:5173/callback", "http://localhost:5173/"]
        logout_urls = _as_list(cfg.get("logoutUrls")) or [
            "http://localhost:5173/", "http://localhost:5173/logout"]
        domain_prefix = cfg.get("cognitoDomainPrefix") or f"kirodash-{self.account}"

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
        cognito.CfnUserPoolGroup(
            self, "AdminsGroup",
            user_pool_id=pool.user_pool_id,
            group_name="admins",
            description="Kiro 看板管理员（可访问全量数据）",
        )

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
