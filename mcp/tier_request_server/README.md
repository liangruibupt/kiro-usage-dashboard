# kiro-tier-request MCP server

> ## ⚠️ 已废弃（DEPRECATED）—— 改用托管的「远程 MCP」
>
> 本地 stdio server 需要每个用户下载代码、装依赖、你一改就得通知所有人更新，
> 版本管控繁琐。现已改为**托管的远程 MCP 端点**（Streamable HTTP）：用户 `mcp.json`
> 只填一个 URL + `X-Api-Key` 头，**本机零代码、零依赖、零更新**，逻辑集中在后端，
> 版本管控 = 重部署。
>
> **请改用以下配置**（远程端点，服务于看板 API 的 `POST /mcp`）：
>
> ```json
> {
>   "mcpServers": {
>     "kiro-tier-request": {
>       "url": "https://<api-id>.execute-api.<region>.amazonaws.com/mcp",
>       "headers": {
>         "X-Api-Key": "<shared-api-key>",
>         "X-Requester-Email": "<本机用户邮箱>"
>       },
>       "disabled": false,
>       "autoApprove": ["request_tier_change"]
>     }
>   }
> }
> ```
>
> 远程实现见 `backend/api/mcp_server.py`（无状态手写 Streamable HTTP，适配 Lambda）。
> 申请人身份：服务端**优先读请求头 `X-Requester-Email`**（每台机器在 `mcp.json`
> 的 headers 里配一次，等价于本地版的 env `REQUESTER_EMAIL`，模型无需每次填邮箱）；
> 若未配置该头，则回退到工具参数 `requester_email`。header 邮箱与本地版 env 一样
> **非验证身份令牌**（可自填），管理员人工审批为最终背书。
>
> 下文为本地 stdio server 的历史文档，仅作参考，不再推荐使用。

---

## （历史）本地 stdio server

轻量的 stdio MCP server，向 Kiro IDE 暴露**单个**工具，用于申请变更 Kiro
订阅档位。基于官方 `mcp` 包 + Python 标准库 `urllib`，依赖极简，**每台机器配置一次**。

配置完成后，用户可在 IDE 内以自然语言触发（例如「帮我把档位升到 Pro Max」），
由 IDE 调用 `request_tier_change` 工具，将申请提交到看板后端，等待管理员人工审批。

## 概述（Overview）

- **组件类型**：独立分发的 stdio MCP server（`mcp/tier_request_server/`）。
- **对外能力**：仅暴露 `request_tier_change(target_tier, reason?)` 一个工具。
- **数据流**：IDE 调用工具 → 本地按允许清单归一化档位 → 携带共享 `X-Api-Key`
  头 `POST` 到看板公开提交端点 → 依响应返回 IDE 友好文案。
- **后端契约**：`POST /api/tier-requests`，请求体 `{requester_email, target_tier, reason?}`。

### 工具：`request_tier_change(target_tier, reason?)`

申请变更 Kiro 订阅档位（`Pro` / `Pro+` / `Pro Max` / `Power`）。

- `target_tier`（必填）：目标档位。本地按允许清单归一化，接受常见拼写与大小写
  （如 `pro+`、`Pro Max`、`promax`、`pro-max`）。非法档位直接返回友好错误，
  **不**调用后端（R54）。
- `reason`（可选）：申请理由。

归一化通过后，携带 `X-Api-Key` 头 `POST` 到 `TIER_REQUEST_ENDPOINT`，
请求体为 `{requester_email, target_tier, reason?}`（R55），并依响应返回 IDE
友好确认或错误文案（如「已提交调档申请：Pro Max，等待管理员处理」）。

## 环境变量（Environment variables）

工具从 MCP server 配置读取以下三项环境变量（R52）：

| 变量 | 含义 | 说明 |
| --- | --- | --- |
| `REQUESTER_EMAIL` | 本机用户身份 | 作为 `requester_email` 提交；反映本机用户，每台机器配置一次。**非**用户提交时手工填写，也**非**经过验证的身份令牌（详见下方安全说明）。 |
| `API_KEY` | 共享 API key | 写入 `X-Api-Key` 请求头；与后端共享的同一把 key，用于挡随机公网滥用。 |
| `TIER_REQUEST_ENDPOINT` | 提交端点完整 URL | 指向 ApiStack 输出的 API endpoint，形如 `https://<api-id>.execute-api.<region>.amazonaws.com/api/tier-requests`（`POST`）。 |

### 配置步骤（step-by-step）

1. 从管理员处获取三项配置值：
   - `REQUESTER_EMAIL`：填本机使用者本人的邮箱。
   - `API_KEY`：管理员分发的共享 key（所有客户端相同）。
   - `TIER_REQUEST_ENDPOINT`：管理员提供的看板 API endpoint 完整 URL。
2. 打开 Kiro IDE 的 MCP 配置文件 `mcp.json`（见下方示例）。
3. 在 `mcpServers.kiro-tier-request.env` 中填入上述三项。
4. 保存并重载 MCP 配置，IDE 即可发现并调用 `request_tier_change` 工具。

## 安装与分发（Distribution / installation）

该 MCP server 作为独立小包随内部文档下发，每台机器安装并配置一次。

方式一 · 以包安装（推荐）：

```bash
# 在 mcp/tier_request_server/ 的上级目录（含 pyproject.toml 的目录）
pip install .
# 或直接安装依赖后从源码运行
pip install -r requirements.txt
```

方式二 · 直接从源码运行（无需安装）：

```bash
# 确保 mcp 包可用，并将包所在目录加入 PYTHONPATH
pip install "mcp>=1.2.0"
python -m tier_request_server.server
```

> 运行入口为 stdio 传输：`python -m tier_request_server.server`。
> 通常无需手工运行，Kiro IDE 会依据 `mcp.json` 中的 `command`/`args` 自动拉起进程。

## Kiro IDE 配置示例（`mcp.json`）

用户在 Kiro IDE 侧配置一次：

```json
{
  "mcpServers": {
    "kiro-tier-request": {
      "command": "python",
      "args": ["-m", "tier_request_server.server"],
      "env": {
        "REQUESTER_EMAIL": "alice@example.com",
        "API_KEY": "<shared-api-key>",
        "TIER_REQUEST_ENDPOINT": "https://<api-id>.execute-api.<region>.amazonaws.com/api/tier-requests"
      }
    }
  }
}
```

## 安全 / 威胁模型——已接受的限制（Security / threat model — accepted limitations）

本功能的威胁模型限定为**输入错误 / 误操作（typos/mistakes）**，**不**将
「恶意冒充他人身份」纳入防护目标；管理员的人工审批为最终背书（backstop）。以下为
明确接受的限制（R48–R50）：

- **共享 API key + 限流仅防随机公网滥用**：提交端点启用共享 `API_KEY` 与限流，
  仅用于阻挡随机的公网滥用，**不**提供「按用户维度的身份冒充」防护——此为明确
  接受的限制（accepted limitation）（R49）。
- **`REQUESTER_EMAIL` 非验证身份令牌**：`requester_email` 取自 MCP server 配置的
  环境变量 `REQUESTER_EMAIL`（每台机器配置一次），**非**用户提交时手工填写，且
  **不**应被视为经过验证的身份令牌（verified identity token）——可被伪填（R50）。
- **管理员人工审批为最终背书**：由于上述机制不能防止身份冒充，最终由管理员在
  看板中人工审批（approve / close）作为背书，确保每次档位变更均经过人工把关（R48）。

补充说明：提交端点是唯一公开路由，且**仅** `POST /api/tier-requests`；其余 GET /
close 等操作仍走 Cognito JWT + admins 组鉴权。SES 通知已出沙盒（可发任意收件人），
审批相关的写操作会记录审计日志。
