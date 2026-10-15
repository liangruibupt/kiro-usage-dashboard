# 部署最小权限策略说明（`least-privilege-deploy-policy.json`）

IAM 策略 JSON 不支持注释字段（`_comment` 之类会被 Access Analyzer 报 `INVALID_POLICY_ELEMENT`），
故把设计取舍写在这里。

## 用途

`least-privilege-deploy-policy.json` 只授予**执行 `cdk bootstrap` / `cdk deploy`（以及可选的
`backend/agent/deploy.py`）的部署身份**（IAM 用户或角色）。它不是运行时权限：CDK 栈创建的各 Lambda
执行角色、Agent Runtime 执行角色都有各自按资源收敛的策略，与本文件无关。

## 有意保留动作级通配的 7 条语句

| Sid | 动作 | 资源范围 |
|---|---|---|
| `CloudFormationAppAndToolkitStacks` | `cloudformation:*` | 栈 `KiroDash-*`、`CDKToolkit` |
| `S3AppAndCdkBuckets` | `s3:*` | 桶 `kirodash-*`、`cdk-hnb659fds-*` |
| `LambdaAppFunctionsAndLayers` | `lambda:*` | 函数 `KiroDash-*`、layer |
| `CognitoUserPools` | `cognito-idp:*` | user pool |
| `ApiGatewayHttpApis` | `apigateway:*` | HTTP API（`/apis`、`/apis/*`、`/tags/*`） |
| `CloudFrontDistributionResources` | `cloudfront:*` | distribution / OAC / response-headers-policy / cache-policy |
| `EcrCdkAndAgentRepos` | `ecr:*` | 仓库 `cdk-hnb659fds-container-assets-*`、`kiro-*` |

这些语句**资源已限定**到本应用（`KiroDash-*`）与 CDK bootstrap（`cdk-hnb659fds-*` / `CDKToolkit`）的命名模式，
但**动作是服务级通配**：CloudFormation 在 create / update / rollback / delete 过程中调用的动作集合很大，
且随 CDK 与 CloudFormation 资源处理程序版本变化，事先枚举容易在回滚或删除栈时出现 `AccessDenied`，
导致栈卡在 `UPDATE_ROLLBACK_FAILED` 等状态。

**生产环境建议**：完成一次完整部署（含一次更新与一次删除）后，用 CloudTrail 记录的实际调用
（可借助 [IAM Access Analyzer 策略生成](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-analyzer-policy-generation.html)）
把上述 7 条语句替换为精确动作列表。

## 其余语句均为动作 + 资源双重收敛

- DynamoDB（表 `KiroDash-*`）、Athena（workgroup `*-wg`）、Glue（库 `kiro_usage` 及其表）、
  Secrets Manager（`kiro-dashboard/*`）、EventBridge（规则 `KiroDash-*`）、CloudWatch Logs
  （日志组 `/aws/lambda/KiroDash-*`、`KiroDash-*`）、AgentCore Runtime（`runtime/*`）、IAM 角色/策略
  （`KiroDash-*` / `cdk-hnb659fds-*` / `KiroDashAgentRuntimeRole`）、SSM bootstrap 参数、
  `sts:AssumeRole`（CDK bootstrap 角色）、Kiro 源桶只读：均为精确动作 + 限定资源。
- `iam:PassRole` 限定到上述角色，并以 `iam:PassedToService` 条件限定只能传给
  lambda / events / cloudformation / bedrock-agentcore。
- 仅以下两条保留 `"Resource": "*"`：
  - `AccountWideReadOnlyAndUnscopable`：不支持资源级权限的动作（只读/列举，或 CDK bootstrap 需要的
    创建期调用），如 `cloudformation:ValidateTemplate`、`cognito-idp:CreateUserPool`、
    `ecr:GetAuthorizationToken`、`logs:DescribeLogGroups`、`secretsmanager:GetRandomPassword`、
    `bedrock-agentcore:ListAgentRuntimes`、`sts:GetCallerIdentity`。
  - `IdentityCenterReadOnly`：Identity Store / SSO 的只读 List/Describe 动作。

## 参考

- [AWS CDK bootstrapping](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html)
- [Customize AWS CDK bootstrapping](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping-customizing.html)
  （收敛 CloudFormation 执行角色权限、信任策略等）
- [Create and apply permissions boundaries for the AWS CDK](https://docs.aws.amazon.com/cdk/v2/guide/customize-permissions-boundaries.html)
