"""WebStack：前端静态站点（S3 私有桶 + CloudFront + OAC）。

- 私有 S3 桶（BLOCK_ALL），仅 CloudFront 通过 OAC 访问（无公网直读）。
- CloudFront：默认根对象 index.html；SPA 路由回退（403/404 -> /index.html, 200）。
- 安全响应头（HSTS / X-Content-Type-Options / frame-deny）。
- 部署 frontend/dist 静态资源；config.json 由部署脚本在拿到 CloudFront 域后写入。

依赖：frontend 已 `npm run build` 生成 dist/。CloudFront 域名在部署后才已知，
故 Cognito 回调 URL 与 dist/config.json 的最终值由 deploy 流程回填（见 README/部署脚本）。
"""
from __future__ import annotations

import os

from aws_cdk import (
    Stack, RemovalPolicy, Duration, CfnOutput,
    aws_s3 as s3,
    aws_cloudfront as cf,
    aws_cloudfront_origins as origins,
    aws_s3_deployment as s3d,
)
from constructs import Construct

FRONTEND_DIST = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))


class WebStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, cfg: dict, **kw):
        super().__init__(scope, cid, **kw)

        # --- 私有站点桶 ---
        site = s3.Bucket(
            self, "SiteBucket",
            removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED)

        # --- 安全响应头 ---
        headers = cf.ResponseHeadersPolicy(
            self, "SecHeaders",
            security_headers_behavior=cf.ResponseSecurityHeadersBehavior(
                content_type_options=cf.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cf.ResponseHeadersFrameOptions(
                    frame_option=cf.HeadersFrameOption.DENY, override=True),
                referrer_policy=cf.ResponseHeadersReferrerPolicy(
                    referrer_policy=cf.HeadersReferrerPolicy.SAME_ORIGIN, override=True),
                strict_transport_security=cf.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365),
                    include_subdomains=True, override=True),
            ))

        # --- CloudFront + OAC（S3BucketOrigin.with_origin_access_control）---
        dist = cf.Distribution(
            self, "Distribution",
            default_root_object="index.html",
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=headers,
            ),
            # SPA：客户端路由，未命中的路径回退到 index.html
            error_responses=[
                cf.ErrorResponse(http_status=403, response_http_status=200,
                                 response_page_path="/index.html", ttl=Duration.minutes(5)),
                cf.ErrorResponse(http_status=404, response_http_status=200,
                                 response_page_path="/index.html", ttl=Duration.minutes(5)),
            ],
            comment="Kiro usage dashboard SPA",
        )

        # --- 部署 dist（若已构建）---
        if os.path.isdir(FRONTEND_DIST):
            s3d.BucketDeployment(
                self, "DeploySite",
                sources=[s3d.Source.asset(FRONTEND_DIST)],
                destination_bucket=site,
                distribution=dist,
                distribution_paths=["/*"],  # 部署后失效缓存
            )

        self.distribution = dist
        self.site_bucket = site
        CfnOutput(self, "CloudFrontDomain", value=f"https://{dist.distribution_domain_name}")
        CfnOutput(self, "SiteBucketName", value=site.bucket_name)
        CfnOutput(self, "DistributionId", value=dist.distribution_id)
