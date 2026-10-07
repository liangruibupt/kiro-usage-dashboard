import { useCallback, useEffect, useState } from "react";
import { Alert, App as AntdApp, Button, Card, Input, Modal, Popconfirm, Space, Spin, Table, Tag } from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { api, ApiAuthError } from "../api/client";
import { login } from "../auth/auth";
import type { AdminUser } from "../api/types";
import { AppShell } from "../components/AppShell";
import { useI18n } from "../i18n";

const STATUS_MAP: Record<string, { key: string; color: string }> = {
  CONFIRMED: { key: "admins.status.active", color: "green" },
  FORCE_CHANGE_PASSWORD: { key: "admins.status.pending", color: "orange" },
  RESET_REQUIRED: { key: "admins.status.pending", color: "orange" },
};

export function AdminManagementPage() {
  const { t } = useI18n();
  const { message } = AntdApp.useApp();
  const [rows, setRows] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [onboardOpen, setOnboardOpen] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .adminListUsers()
      .then((r) => setRows(r.users))
      .catch((e) => {
        if (e instanceof ApiAuthError) return login();
        setError(String(e));
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const runAction = async (fn: () => Promise<unknown>, okMsg: string) => {
    setBusy(true);
    try {
      await fn();
      message.success(okMsg);
      load();
    } catch (e) {
      if (e instanceof ApiAuthError) return login();
      message.error(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onboard = async () => {
    const email = newEmail.trim().toLowerCase();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      message.error(t("admins.onboard.emailPlaceholder"));
      return;
    }
    await runAction(() => api.adminCreateUser(email), t("admins.done"));
    setOnboardOpen(false);
    setNewEmail("");
  };

  const columns: ColumnsType<AdminUser> = [
    { title: t("admins.col.email"), dataIndex: "email" },
    {
      title: t("admins.col.status"),
      dataIndex: "status",
      width: 140,
      render: (s: string, r) => {
        if (!r.enabled) return <Tag color="default">{t("admins.status.disabled")}</Tag>;
        const m = STATUS_MAP[s];
        return <Tag color={m?.color ?? "blue"}>{m ? t(m.key) : s}</Tag>;
      },
    },
    {
      title: t("admins.col.actions"),
      key: "actions",
      width: 320,
      render: (_v, r) => (
        <Space wrap>
          <Popconfirm title={t("admins.confirm.reset")} okText={t("admins.ok")} cancelText={t("admins.cancel")}
            onConfirm={() => runAction(() => api.adminResetPassword(r.email), t("admins.done"))}>
            <Button size="small" disabled={busy}>{t("admins.action.reset")}</Button>
          </Popconfirm>
          {(r.status === "FORCE_CHANGE_PASSWORD" || !r.enabled) && (
            <Button size="small" disabled={busy}
              onClick={() => runAction(() => api.adminResendInvite(r.email), t("admins.done"))}>
              {t("admins.action.resend")}
            </Button>
          )}
          <Popconfirm title={t("admins.confirm.remove")} okText={t("admins.ok")} cancelText={t("admins.cancel")}
            onConfirm={() => runAction(() => api.adminRemoveUser(r.email), t("admins.done"))}>
            <Button size="small" danger disabled={busy}>{t("admins.action.remove")}</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <AppShell active="admins">
      <div style={{ marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <Button icon={<ReloadOutlined />} onClick={load}>{t("common.refresh")}</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOnboardOpen(true)}>
          {t("admins.onboard")}
        </Button>
      </div>

      {error && <Alert type="error" showIcon message={t("common.loadFailed")} description={error} style={{ marginBottom: 16 }} />}

      <Card size="small" title={t("admins.title")}>
        <Spin spinning={loading}>
          <Table<AdminUser> rowKey="email" size="small" columns={columns} dataSource={rows}
            pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [20, 50, 100] }}
            scroll={{ x: 700 }} />
        </Spin>
      </Card>

      <Modal
        title={t("admins.onboard")}
        open={onboardOpen}
        onOk={onboard}
        confirmLoading={busy}
        onCancel={() => setOnboardOpen(false)}
        okText={t("admins.ok")}
        cancelText={t("admins.cancel")}
      >
        <Input
          placeholder={t("admins.onboard.emailPlaceholder")}
          value={newEmail}
          onChange={(e) => setNewEmail(e.target.value)}
          onPressEnter={onboard}
          style={{ marginBottom: 8 }}
        />
        <div style={{ color: "#94a3b8", fontSize: 12 }}>{t("admins.onboard.hint")}</div>
      </Modal>
    </AppShell>
  );
}
