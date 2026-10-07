import { useCallback, useEffect, useState } from "react";
import { Alert, App as AntdApp, Button, Card, Input, Modal, Radio, Spin, Table, Tag, Typography } from "antd";
import { ReloadOutlined, CopyOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { api, ApiAuthError } from "../api/client";
import { login } from "../auth/auth";
import type { McpConfigResponse, TierRequest, TierRequestStatus } from "../api/types";
import { AppShell } from "../components/AppShell";
import { useI18n, tierLabel } from "../i18n";

const STATUS_COLOR: Record<TierRequestStatus, string> = {
  pending: "gold",
  completed: "green",
  rejected: "red",
  superseded: "default",
};

type Outcome = "completed" | "rejected";

/** 根据后端返回的 endpoint/key 生成用户 mcp.json 配置片段。 */
function buildMcpJson(cfg: McpConfigResponse): string {
  const url = cfg.mcp_url || "https://<api-id>.execute-api.<region>.amazonaws.com/mcp";
  const key = cfg.api_key || "<shared-api-key>";
  return JSON.stringify(
    {
      mcpServers: {
        "kiro-tier-request": {
          url,
          headers: {
            "X-Api-Key": key,
            "X-Requester-Email": "<your-email@example.com>",
          },
          disabled: false,
          autoApprove: ["request_tier_change"],
        },
      },
    },
    null,
    2,
  );
}

/** 右栏：MCP 配置分发指引（admins 可见，含真实 URL + API key）。 */
function McpConfigPanel() {
  const { t } = useI18n();
  const { message } = AntdApp.useApp();
  const [cfg, setCfg] = useState<McpConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api
      .tierRequestMcpConfig()
      .then(setCfg)
      .catch((e) => {
        if (e instanceof ApiAuthError) return login();
        // 配置拉取失败不致命：仍展示占位模板
        setCfg({ api_endpoint: "", mcp_url: "", api_key: "" });
      })
      .finally(() => setLoading(false));
  }, []);

  const snippet = cfg ? buildMcpJson(cfg) : "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      message.success(t("tierReq.mcp.copied"));
      setTimeout(() => setCopied(false), 2000);
    } catch {
      message.error("Copy failed");
    }
  };

  const stepStyle = { color: "#cbd5e1", fontSize: 13, marginBottom: 8 };
  return (
    <Card size="small" title={t("tierReq.mcp.title")} style={{ flex: "0 0 440px", alignSelf: "flex-start" }}>
      <Spin spinning={loading}>
        <Typography.Paragraph style={{ color: "#94a3b8", fontSize: 13 }}>
          {t("tierReq.mcp.intro")}
        </Typography.Paragraph>
        <div style={stepStyle}>{t("tierReq.mcp.step1")}</div>
        <img
          src="mcp-config.jpg"
          alt={t("tierReq.mcp.imgAlt")}
          style={{
            width: "100%",
            border: "1px solid #1e2a44",
            borderRadius: 6,
            margin: "4px 0 12px",
            display: "block",
          }}
        />
        <div style={stepStyle}>{t("tierReq.mcp.step2")}</div>
        <div style={stepStyle}>{t("tierReq.mcp.step3")}</div>

        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 6 }}>
          <Button size="small" icon={<CopyOutlined />} onClick={copy}>
            {copied ? t("tierReq.mcp.copied") : t("tierReq.mcp.copy")}
          </Button>
        </div>
        <pre
          style={{
            background: "#0b1220",
            border: "1px solid #1e2a44",
            borderRadius: 6,
            padding: 12,
            fontSize: 12,
            lineHeight: 1.5,
            color: "#e2e8f0",
            overflowX: "auto",
            margin: 0,
          }}
        >
          {snippet}
        </pre>
        <div style={{ color: "#94a3b8", fontSize: 12, marginTop: 8 }}>{t("tierReq.mcp.emailHint")}</div>
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 10 }}
          message={t("tierReq.mcp.secWarn")}
        />
      </Spin>
    </Card>
  );
}

export function TierRequestsPage() {
  const { t } = useI18n();
  const { message } = AntdApp.useApp();
  const [rows, setRows] = useState<TierRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState(""); // 按邮箱客户端过滤

  // 关闭申请的 Modal 状态
  const [closeTarget, setCloseTarget] = useState<TierRequest | null>(null);
  const [outcome, setOutcome] = useState<Outcome>("completed");
  const [note, setNote] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .tierRequestsList()
      .then((r) => setRows(r.items))
      .catch((e) => {
        if (e instanceof ApiAuthError) return login();
        setError(String(e));
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openClose = (r: TierRequest) => {
    setCloseTarget(r);
    setOutcome("completed");
    setNote("");
  };

  const submitClose = async () => {
    if (!closeTarget) return;
    setBusy(true);
    try {
      await api.tierRequestClose(closeTarget.id, outcome, note.trim() || undefined);
      message.success(t("admins.done"));
      setCloseTarget(null);
      setNote("");
      load();
    } catch (e) {
      if (e instanceof ApiAuthError) return login();
      message.error(String(e));
    } finally {
      setBusy(false);
    }
  };

  // 按邮箱客户端过滤（大小写不敏感；空搜索返回全部）。
  const filtered = rows.filter((r) =>
    r.requester_email.toLowerCase().includes(search.trim().toLowerCase()));

  const columns: ColumnsType<TierRequest> = [
    { title: t("tierReq.col.requester"), dataIndex: "requester_email", width: 210, ellipsis: true },
    {
      title: t("tierReq.col.name"),
      dataIndex: "requester_name",
      width: 90,
      ellipsis: true,
      render: (v: string | null | undefined) => v || "-",
    },
    {
      title: t("tierReq.col.department"),
      dataIndex: "department",
      width: 110,
      ellipsis: true,
      render: (v: string | null | undefined) => v || "-",
    },
    {
      title: t("tierReq.col.target"),
      dataIndex: "target_tier",
      width: 110,
      render: (v: string) => <Tag>{tierLabel(v, true)}</Tag>,
    },
    { title: t("tierReq.col.reason"), dataIndex: "reason", width: 160, ellipsis: true, render: (v: string | null) => v ?? "" },
    {
      title: t("tierReq.col.status"),
      dataIndex: "status",
      width: 90,
      render: (s: TierRequestStatus) => (
        <Tag color={STATUS_COLOR[s] ?? "default"}>{t(`tierReq.status.${s}`)}</Tag>
      ),
    },
    {
      title: t("tierReq.col.time"),
      dataIndex: "created_at",
      width: 150,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: t("tierReq.col.actions"),
      key: "actions",
      width: 90,
      render: (_v, r) =>
        r.status === "pending" ? (
          <Button size="small" disabled={busy} onClick={() => openClose(r)}>
            {t("tierReq.action.close")}
          </Button>
        ) : null,
    },
  ];

  return (
    <AppShell active="tierRequests">
      <div style={{ marginBottom: 16, display: "flex", justifyContent: "flex-start", alignItems: "center", gap: 12 }}>
        <Button icon={<ReloadOutlined />} onClick={load}>{t("common.refresh")}</Button>
      </div>

      {error && <Alert type="error" showIcon message={t("common.loadFailed")} description={error} style={{ marginBottom: 16 }} />}

      {/* 左右两栏：左=申请列表，右=MCP 配置分发指引 */}
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        <Card
          size="small"
          title={t("tierReq.title")}
          style={{ flex: "1 1 560px", minWidth: 0 }}
          extra={
            <Input.Search
              allowClear
              placeholder={t("tierReq.search")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 240 }}
            />
          }
        >
          <Spin spinning={loading}>
            <Table<TierRequest> rowKey="id" size="small" columns={columns} dataSource={filtered}
              pagination={{ defaultPageSize: 20 }}
              scroll={{ x: "max-content" }} />
          </Spin>
        </Card>

        <McpConfigPanel />
      </div>

      <Modal
        title={t("tierReq.action.close")}
        open={closeTarget !== null}
        onOk={submitClose}
        confirmLoading={busy}
        onCancel={() => setCloseTarget(null)}
        okText={t("admins.ok")}
        cancelText={t("admins.cancel")}
      >
        <Radio.Group
          value={outcome}
          onChange={(e) => setOutcome(e.target.value as Outcome)}
          style={{ marginBottom: 12 }}
        >
          <Radio.Button value="completed">{t("tierReq.action.completed")}</Radio.Button>
          <Radio.Button value="rejected">{t("tierReq.action.rejected")}</Radio.Button>
        </Radio.Group>
        <Input.TextArea
          placeholder={t("tierReq.note.placeholder")}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
        />
      </Modal>
    </AppShell>
  );
}
