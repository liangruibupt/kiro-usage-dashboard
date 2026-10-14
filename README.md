# Kiro 使用效能看板（kiro-usage-dashboard）

面向管理员的 **AWS Kiro 订阅用量分析看板**：汇总并可视化企业内 Kiro（Q Developer）
的使用情况，支持自然语言问答、模型集中度分析、月度调档建议（升/降档 + 建议关停）、
管理中心（管理员与模型费率维护），以及用户经 IDE 发起的 **订阅档位调整申请**。
整套方案 Serverless，可一键部署到你自己的 AWS 账户。

> 演示模式（`mockMode=true`）无需任何真实数据源即可跑起来，适合先快速体验。

---

## 一、主要功能

### 1. 用量看板（Dashboard）
- **KPI 概览**：注册 / 活跃用户数、激活率、日活（均值/峰值）、总 Credits、超额用户数、预估成本、人均 Credits。
- **订阅档位分布**：Pro / Pro+ / Pro Max / Power 四档人数分布。
- **图表**：每日 Credits 消耗趋势（双轴）、模型使用环形图、各部门 Credits 对比 / 人均、Top N 用户横向柱状图。
- **用户明细表**：按部门筛选、排序、分页，含使用率进度条与状态标签（正常/接近上限/超额/未使用）；可下钻单用户的模型使用量。
- **筛选器**：按数据周期（月）或自定义日期范围过滤。
- **中英双语**：界面一键切换 zh / en。

### 2. 模型集中度分析
独立页，分析员工对「贵模型」的使用集中度：多选模型 + 直方图展示每位员工「所选模型消息数占个人全部模型消息数」的占比分布；
点击直方图任意区间列即可下钻查看该区间的人员明细（姓名/部门/邮箱/消息数/占比），可导出 CSV。
默认预选 credit 倍率 ≥2.0x 的模型，帮助了解是否存在过度使用高倍率模型的情况。

### 3. 自然语言问答（NL Q&A）
看板内置对话框，用自然语言提问（如「本月哪个部门 Credits 最高？」「谁触发了超额？」），
由部署在 **Amazon Bedrock AgentCore Runtime** 的 Agent 调用受控查询工具作答，并标注数据口径与时间范围。

### 4. 月度使用报告（调档建议 + 建议关停 + 自动邮件）
- 看板内「月度报告」页按使用率给出**升档 / 降档 / 建议关停**三类名单，每类都有**可拖动的阈值滑块**
  （默认升档 ≥80%、降档 <40%、关停 ≤0% 即零用量），拖动即时更新名单；升/降档给出建议档位（升/降一档）。
- **建议关停**：列出低用量订阅；并识别**同一员工的多个订阅**（按 Identity Center 的 Employee number 归并，
  保留最早创建的订阅、其余建议下月关停），帮助清理冗余席位。
- 每月月末前自动经 **Amazon SES** 发送当月使用报告给管理员（含汇总 KPI 与调档建议）；报告也可在页面查看并下载 CSV。

### 5. 管理中心（管理员管理 + 费率维护）
看板内「管理中心」页用左侧菜单组织多项管理功能，便于后续扩展：
- **管理员管理**：onboard 新管理员（自动创建 Cognito 用户 + 加入 `admins` 组、发送邀请邮件）、重置密码、重发邀请、移除。危险操作有二次确认。
- **费率维护**：维护各模型的 Kiro credit 倍率（预置官方倍率，可按模型手动编辑）；模型集中度分析的「贵模型」预选即以此为准。

### 6. 订阅调档申请（Tier change requests）
普通用户在 **Kiro IDE** 内经 MCP server 用自然语言发起调档申请（如「帮我升到 Pro Max」），
后端收集 / 去重 / 存储并邮件通知；管理员在看板「调档申请」页处理（完成 / 驳回）并经 SES
回执申请人。**定位为「收集与通知」，不自动执行真实调档**，最终由管理员人工审批。
- 远程 MCP 端点（Streamable HTTP）托管在看板 API 上（`POST /mcp`），用户 IDE 只需配一个 URL + 请求头，**无需在本机安装任何代码**。
- 详见下文「三、在 Kiro IDE 中使用调档申请」。

---

## 二、部署指南

把整套看板部署到你自己的 AWS 账户。下文用占位符：账户 `<ACCOUNT_ID>`、区域 `<REGION>`、
AWS CLI profile `<PROFILE>`。

### 1. 前置条件
- **Node.js ≥ 18** 与 **AWS CDK CLI**（`npm install -g aws-cdk` 或用 `npx cdk`）
- **Python 3.12**
- **AWS CLI v2**，并配置目标账户凭证：
  ```bash
  aws configure --profile <PROFILE>
  aws sts get-caller-identity --profile <PROFILE>   # 确认账户正确
  ```
- （真实模式）目标账户已有 Kiro / Q Developer 企业订阅，且每日用量 CSV 导出到某个 S3 桶；
  若接入 Identity Center 取部门/订阅名单，还需 SSO 实例信息。演示可跳过（见 mock 模式）。

#### 部署所需的最小 IAM 权限

部署用的 IAM 身份（**用户或角色均可，名字任意**）需要一组权限来跑 `cdk bootstrap` / `cdk deploy`
及（可选）Agent 部署。仓库提供了一份现成的最小权限策略：`docs/least-privilege-deploy-policy.json`。

> 这是一份**单份**客户托管策略（压缩后约 3.3KB，在 IAM 托管策略 6144 字节上限内）。它按服务收敛
> 权限（不是 `*:*`）；对需要大量动作的服务（`cloudformation` / `s3` / `lambda` / `cognito-idp` /
> `apigateway` / `cloudfront` / `ecr`）用服务级通配，但资源限定到本应用的命名模式：栈
> `KiroDash-*` 与 `CDKToolkit`、桶 `kirodash-*` 与 `cdk-hnb659fds-*`、函数 `KiroDash-*`、
> DynamoDB 表 `KiroDash-*`、Athena workgroup `*-wg`、IAM 角色/策略 `KiroDash-*` / `cdk-hnb659fds-*`
> / `KiroDashAgentRuntimeRole`、ECR 仓库 `cdk-hnb659fds-container-assets-*` 与 `kiro-*`。
> `iam:PassRole` 限定到上述角色，并用条件限定只能传给 lambda/events/cloudformation/bedrock-agentcore。
>
> `AccountWideReadOnlyAndUnscopable` 语句保留 `"Resource": "*"`：these actions do not support
> resource-level scoping; they are read/describe or create-time calls required by CDK bootstrap
> （如 `cloudformation:ValidateTemplate`、`cognito-idp:CreateUserPool`、`ecr:GetAuthorizationToken`、
> `sts:GetCallerIdentity`）。`StarResourceServices` 中的 glue / secretsmanager / events / logs /
> bedrock-agentcore / identitystore / sso 精确动作列表仍为 `*`。IAM 策略不支持 `_comment` 之类的
> 自定义字段（Access Analyzer 报 `INVALID_POLICY_ELEMENT`），故说明写在此处。
>
> 若部署 NL 问答 Agent，Agent 镜像的 ECR 仓库请以 `kiro-` 开头命名（如 `kiro-usage-agent`）。
>
> **关于通配符的说明：** `docs/least-privilege-deploy-policy.json` 是一份部署期的管理性 bootstrap 策略，
> 仅供执行 `cdk deploy` 的身份使用。其中若干服务级通配动作（`cloudformation` / `s3` / `lambda` /
> `cognito-idp` / `apigateway` / `cloudfront` / `ecr`）以及部分 create/delete 动作是有意放宽的：
> 具体资源 ARN 要等 CDK 在同一次部署中创建后才存在，无法事先写死。用于生产前，请按你的部署实际需要
> 收敛到具体动作与资源 ARN（参见 AWS CDK 关于 bootstrap 最小权限的官方指引）。CDK 栈创建的运行时
> Lambda / 角色策略本身已按资源收敛。

**使用步骤：**

1. **填占位符**：把策略里的 `<KIRO_SOURCE_BUCKET>` 替换为你的 Kiro 每日用量源桶名（真实模式）。
   mock 模式（`mockMode=true`）不读外部源桶，可直接删除 `KiroSourceBucketReadCrossRegion` 整条语句。

2. **创建托管策略**（替换 `<PROFILE>`）：
   ```bash
   aws iam create-policy --profile <PROFILE> \
     --policy-name kiro-dashboard-deploy \
     --policy-document file://docs/least-privilege-deploy-policy.json
   ```

3. **附加到你的部署身份**（用户或角色，取上一步输出的 Policy ARN）：
   ```bash
   # 附加给 IAM 用户
   aws iam attach-user-policy --profile <PROFILE> \
     --user-name <YOUR_DEPLOY_USER> --policy-arn <POLICY_ARN>
   # 或附加给 IAM 角色
   aws iam attach-role-policy --profile <PROFILE> \
     --role-name <YOUR_DEPLOY_ROLE> --policy-arn <POLICY_ARN>
   ```

4. 之后用该身份的凭证执行下文的 bootstrap / deploy 即可。验证标准：全程无 `AccessDenied`，
   各栈 `CREATE_COMPLETE`。

> 不部署 NL 问答 Agent 时，可从策略中删除 `ecr:*` 与 `bedrock-agentcore:*` 两类动作以进一步收敛。
> 若追求更严格的最小化，可在首次部署后把服务级通配替换为实际生成资源的 ARN（会牺牲重复清栈重建的灵活性）。

### 2. Python 环境
```bash
cd kiro-dashboard-on-agentcore
python3.12 -m venv .venv
.venv/bin/pip install -r infra/requirements.txt
# 可选（本地跑后端单测）：
.venv/bin/pip install pytest boto3 pandas pyarrow fastapi mangum
```
> CDK app 用 `.venv` 里的 Python 执行，后续命令带 `--app "../.venv/bin/python app.py"`。

### 3. 填写部署参数
编辑 `infra/cdk.json` 的 `context`（字段含义与示例见 `infra/cdk.context.example.json`）。
关键参数：

| 参数 | 说明 |
|------|------|
| `deployRegion` | 部署区域，如 `us-east-1` |
| `mockMode` | `true`=用 mock 数据演示（可留空下方数据源字段）；生产填 `false` |
| `kiroSourceBucket` / `kiroSourcePrefix` | Kiro 用量 CSV 所在 S3 桶与前缀（真实模式） |
| `identityStoreId` / `identityStoreRegion` / `ssoInstanceArn` / `subscriptionAppArn` | Identity Center / 订阅应用信息（真实模式，取注册名单与部门） |
| `groupCsvBucket` / `groupCsvKey` | 备选部门来源：Kiro 导出的 user→group CSV |
| `creditUnitPrice` | credit 单价（Kiro 官方超额价） |
| `adminEmail` | **必填**：首个管理员邮箱。部署 Auth 栈时自动创建该 Cognito 用户、加入 `admins` 组，并由 Cognito 发送含临时密码的邀请邮件；留空或仍为占位符 `admin@example.com` 时 synth 直接报错阻止部署。 |
| `appUrl` | 看板登录地址（CloudFront 域），写入首个管理员的邀请邮件正文告知去哪登录。首次部署可留空（邮件改为提示向部署人员索取地址），部署 Web 拿到 CloudFront 域后回填再重部署 Auth 栈。 |
| `sesSender` | **已在 SES 验证**的发件邮箱（月度报告 / 调档回执邮件用） |
| `reportRecipients` | （可选）额外报告收件人，逗号分隔；与 admins 组取并集 |
| `agentRuntimeArn` | NL 问答 Agent 的 Runtime ARN；先留空，部署 Agent 后回填 |

> `infra/cdk.json` 与 `frontend/public/config.json` 仓库内为**占位符模板**；填入你账户的真实值后
> **请勿提交这些本地改动**（可用 `git update-index --skip-worktree infra/cdk.json`）。

### 4. Bootstrap（每账户+区域一次）
```bash
cd infra
AWS_PROFILE=<PROFILE> CDK_DEFAULT_REGION=<REGION> \
  npx cdk bootstrap aws://<ACCOUNT_ID>/<REGION> --app "../.venv/bin/python app.py"
```

### 5. 部署 Data / Auth / Api
```bash
export AWS_PROFILE=<PROFILE> CDK_DEFAULT_REGION=<REGION>
npx cdk deploy KiroDash-Data KiroDash-Auth KiroDash-Api --app "../.venv/bin/python app.py" --require-approval never
```
记下输出：`UserPoolId`、`UserPoolClientId`、`HostedUiDomain`、`ApiEndpoint` 等。

### 6. 首次部署 Web（拿到 CloudFront 域）
```bash
cd ../frontend && npm install && npm run build && cd ../infra
npx cdk deploy KiroDash-Web --app "../.venv/bin/python app.py" --require-approval never
```
记下输出 `CloudFrontDomain`。

### 7. 回填回调/CORS 并重部署
拿到 CloudFront 域后，在 `infra/cdk.json` 补 `callbackUrls` / `logoutUrls` / `corsOrigins`，重部署 Auth + Api。
再把 `frontend/public/config.json` 按步骤 5–6 的输出填好（参考 `config.example.json`），
重新 `npm run build` 并 `cdk deploy KiroDash-Web`。

### 8. 初始化数据（真实模式）
```bash
# 同步注册名单/部门到 DynamoDB
aws lambda invoke --function-name <IdentitySyncFnName> --profile <PROFILE> --region <REGION> \
  --cli-binary-format raw-in-base64-out --payload '{}' /tmp/idsync.json && cat /tmp/idsync.json
# 回填历史 CSV → Parquet
aws lambda invoke --function-name <EtlFnName> --profile <PROFILE> --region <REGION> \
  --cli-binary-format raw-in-base64-out --payload '{"backfill": true}' --cli-read-timeout 900 /tmp/etl.json && cat /tmp/etl.json
```

### 9. 首个管理员（自动创建）并登录
部署 Auth 栈时已按 `adminEmail` 自动创建首个管理员并发送邀请邮件，无需手工建号。
拿到 CloudFront 域后把 `appUrl` 回填 `cdk.json` 并重部署 Auth 栈，邀请邮件正文即含登录地址。
首个管理员收到邮件后，用其中的用户名 + 临时密码访问 `https://<cloudfront-domain>` 登录，
首次登录会被要求设置新密码。后续新增管理员见下文「四、管理员与用户」。

### 10.（可选）部署 NL 问答 Agent
需 Docker 构建 arm64 容器推到 ECR，再用 `backend/agent/deploy.py` 部署到 AgentCore Runtime，
把输出的 runtime ARN 回填 `cdk.json` 的 `agentRuntimeArn` 并重部署 `KiroDash-Api`。
不部署时 `/api/chat` 返回「未配置」，其余功能不受影响。

---

## 三、在 Kiro IDE 中使用调档申请（远程 MCP）

用户在自己的 Kiro IDE 配置一个远程 MCP server，即可用自然语言发起调档申请。
**无需在本机安装任何代码**，只需在 `~/.kiro/settings/mcp.json`（或工作区 `.kiro/settings/mcp.json`）
加入下面的配置块：

```json
{
  "mcpServers": {
    "kiro-tier-request": {
      "url": "https://<api-id>.execute-api.<region>.amazonaws.com/mcp",
      "headers": {
        "X-Api-Key": "<shared-api-key>",
        "X-Requester-Email": "<你的邮箱>"
      },
      "disabled": false,
      "autoApprove": ["request_tier_change"]
    }
  }
}
```

- `url`：看板 API 的 `/mcp` 端点（`ApiEndpoint` 输出 + `/mcp`）。
- `X-Api-Key`：共享 API key，由管理员分发（存于部署账户的 Secrets Manager `kiro-dashboard/tier-api-key`）。
- `X-Requester-Email`：填用户本人邮箱（标识申请人；每台机器配一次，之后对话无需再提供邮箱）。

配置后在 Kiro 聊天里说：「帮我申请把 Kiro 档位升到 Pro Max，原因是模型用量增大」，
工具会提交申请，管理员在看板「调档申请」页处理。

**安全/威胁模型（已接受的限制）**：共享 API key + 限流仅用于挡随机公网滥用；`X-Requester-Email`
非验证身份令牌（可自填），**管理员人工审批为最终背书**。请通过可信渠道分发 API key，不要公开。

---

## 四、管理员与用户

看板登录用户 = Cognito User Pool 里的用户，且必须在 `admins` 组（否则接口 403）。
User Pool 已关闭自助注册，由管理员创建用户。密码策略：≥12 位，含大小写、数字、符号。

- **首个管理员**：部署 Auth 栈时按 `adminEmail` 自动创建、入组并发邀请邮件（含登录地址 + 临时密码），
  首次登录改密即可，无需手工操作。
- **推荐**：登录看板 →「管理员管理」页 onboard 新管理员（自动建用户 + 入组 + 发邀请邮件）。
- **后备（AWS CLI）**：
  ```bash
  POOL=<UserPoolId>; EMAIL=admin@example.com
  aws cognito-idp admin-create-user --user-pool-id "$POOL" --username "$EMAIL" \
    --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
    --message-action SUPPRESS --profile <PROFILE> --region <REGION>
  aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL" --username "$EMAIL" \
    --group-name admins --profile <PROFILE> --region <REGION>
  aws cognito-idp admin-set-user-password --user-pool-id "$POOL" --username "$EMAIL" \
    --password '<一个符合策略的密码>' --permanent --profile <PROFILE> --region <REGION>
  ```

> 被「统计」在看板图表里的 Kiro 使用者，不是这里的登录用户——他们由 AWS 侧分配 Kiro 席位 +
> 实际使用后，经 identity-sync / ETL 自动纳入，无需手工添加。

---

## 五、本地开发

```bash
# 后端单测
.venv/bin/python -m pytest backend/tests/ -q
# 前端本地开发（读同目录 public/config.json，可指向已部署的 API/Cognito 联调）
cd frontend && npm install && npm run dev   # http://localhost:5173
```

---

## 六、口径说明（数据准确性）

- **credit 单价**：由 `creditUnitPrice` 配置（Kiro 官方超额/加购价）。
- **派生字段**：usage_rate / balance / 超额判定均基于每用户 `Usage_Limit`（PRO=1000 / PRO+=2000 / PRO_MAX=5000 / POWER=10000）。
- **超额**：区间累计 credits > usage_limit。
- **日活**：区间内每日 distinct 活跃用户的均值 / 峰值。
- **激活率**：活跃用户 / 注册总数（注册总数 = SSO 订阅应用分配的去重人数）。
- **档位**：取用户最近一天的 subscription_tier。

---

## 七、SES 发信前置（月度报告 / 调档回执）

邮件经 Amazon SES 发送，需先在部署区域**验证发件邮箱**（`sesSender`）：
```bash
aws ses verify-email-identity --email-address <sesSender> --profile <PROFILE> --region <REGION>
```
SES 新账户默认处于**沙箱模式**，只能发给已验证的收件人；要发给任意地址需在 SES 控制台申请
生产访问（Production access）。沙箱期间不影响申请提交/审批流程，仅影响邮件触达。

---

## 规格文档

需求 / 设计 / 任务分解见 `.kiro/specs/kiro-usage-dashboard/`（requirements / design / tasks），
记录了本项目的 spec-driven 开发过程。
