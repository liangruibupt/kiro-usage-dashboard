import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Col, Row, Spin, Statistic, Table, Tag } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { Dayjs } from "dayjs";
import { api, ApiAuthError } from "../api/client";
import { login } from "../auth/auth";
import type { FiltersResponse, ReportRecoRow, ReportResponse } from "../api/types";
import { FilterBar, defaultRange, toDateRange, type FilterMode } from "../components/FilterBar";
import { AppShell } from "../components/AppShell";
import { useI18n, tierLabel } from "../i18n";

export function MonthlyReportPage() {
  const { t, lang } = useI18n();
  const numLocale = lang === "en" ? "en-US" : "zh-CN";
  const fmt = (n: number) => n.toLocaleString(numLocale, { maximumFractionDigits: 1 });

  const [mode, setMode] = useState<FilterMode>("range");
  const [period, setPeriod] = useState<string | undefined>(undefined);
  const [periods, setPeriods] = useState<string[]>([]);
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(defaultRange());
  const [tick, setTick] = useState(0);
  const [data, setData] = useState<ReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const dateRange = useMemo(() => toDateRange(mode, period, range), [mode, period, range]);

  useEffect(() => {
    let active = true;
    api
      .filters()
      .then((f: FiltersResponse) => {
        if (!active) return;
        setPeriods(f.periods);
        if (!period && f.periods.length) setPeriod(f.periods[0]);
      })
      .catch(() => {
        /* 周期列表拉取失败不阻塞按日期范围模式 */
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api
      .report(dateRange)
      .then((r) => active && setData(r))
      .catch((e) => {
        if (e instanceof ApiAuthError) {
          login();
          return;
        }
        if (active) setError(String(e));
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [dateRange, tick]);

  const cols: ColumnsType<ReportRecoRow> = [
    {
      title: t("report.col.name"),
      key: "name",
      render: (_v, r) => (
        <div>
          <div>{r.display_name || r.email.split("@")[0]}</div>
          <div style={{ color: "#64748b", fontSize: 12 }}>{r.email}</div>
        </div>
      ),
    },
    { title: t("report.col.department"), dataIndex: "department", width: 160 },
    {
      title: t("report.col.current"),
      dataIndex: "subscription_tier",
      width: 100,
      render: (v: string) => <Tag>{tierLabel(v, true)}</Tag>,
    },
    {
      title: t("report.col.usage"),
      key: "usage",
      width: 150,
      render: (_v, r) => `${fmt(r.credits_used)} / ${fmt(r.usage_limit)}`,
    },
    {
      title: t("report.col.rate"),
      dataIndex: "usage_rate",
      width: 90,
      render: (v: number) => `${(v * 100).toFixed(1)}%`,
    },
    {
      title: t("report.col.suggested"),
      dataIndex: "suggested_tier",
      width: 110,
      render: (v: string) => <Tag color="blue">{tierLabel(v, true)}</Tag>,
    },
  ];

  const downloadCsv = () => {
    if (!data) return;
    const header = ["type", "name", "email", "department", "current_tier", "credits_used", "usage_limit", "usage_rate", "suggested_tier"];
    const lines = [header.join(",")];
    const push = (kind: string, rows: ReportRecoRow[]) => {
      rows.forEach((r) => {
        lines.push([
          kind,
          `"${(r.display_name || "").replace(/"/g, '""')}"`,
          r.email,
          `"${(r.department || "").replace(/"/g, '""')}"`,
          `"${tierLabel(r.subscription_tier)}"`,
          r.credits_used,
          r.usage_limit,
          (r.usage_rate * 100).toFixed(1) + "%",
          `"${tierLabel(r.suggested_tier)}"`,
        ].join(","));
      });
    };
    push("upgrade", data.upgrades);
    push("downgrade", data.downgrades);
    const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const label = data.range.from && data.range.to ? `${data.range.from}_${data.range.to}` : "report";
    a.href = url;
    a.download = `kiro-adjust-${label}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const k = data?.kpis;

  return (
    <AppShell active="report">
      <div style={{ marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <FilterBar
          mode={mode}
          onModeChange={setMode}
          periods={periods}
          period={period}
          onPeriodChange={setPeriod}
          range={range}
          onRangeChange={setRange}
          onRefresh={() => setTick((x) => x + 1)}
        />
        <Button icon={<DownloadOutlined />} onClick={downloadCsv} disabled={!data}>
          {t("report.downloadCsv")}
        </Button>
      </div>

      {error && <Alert type="error" showIcon message={t("common.loadFailed")} description={error} style={{ marginBottom: 16 }} />}

      <Spin spinning={loading}>
        {k && (
          <>
            <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
              <Col xs={12} md={6}><Card size="small"><Statistic title={t("kpi.registeredActive")} value={`${k.registered_users} / ${k.active_users}`} valueStyle={{ fontSize: 20 }} /></Card></Col>
              <Col xs={12} md={6}><Card size="small"><Statistic title={t("kpi.activationRate")} value={(k.activation_rate * 100).toFixed(1)} suffix="%" valueStyle={{ fontSize: 20 }} /></Card></Col>
              <Col xs={12} md={6}><Card size="small"><Statistic title={t("kpi.totalCredits")} value={fmt(k.total_credits)} valueStyle={{ fontSize: 20 }} /></Card></Col>
              <Col xs={12} md={6}><Card size="small"><Statistic title={t("kpi.estCost")} value={`$${fmt(k.est_cost)}`} valueStyle={{ fontSize: 20 }} /></Card></Col>
              <Col xs={12} md={6}><Card size="small"><Statistic title={t("kpi.overageUsers")} value={fmt(k.overage_users)} valueStyle={{ fontSize: 20 }} /></Card></Col>
              <Col xs={12} md={6}><Card size="small"><Statistic title={t("kpi.dailyActive")} value={`${fmt(k.daily_active_avg)} / ${fmt(k.daily_active_peak)}`} suffix={t("kpi.unit.person")} valueStyle={{ fontSize: 20 }} /></Card></Col>
              <Col xs={24} md={12}><Card size="small"><Statistic title={t("tier.title")} value={`${tierLabel("Pro", true)} ${k.tiers.Pro} · ${tierLabel("ProPlus", true)} ${k.tiers.ProPlus} · ${tierLabel("ProMax", true)} ${k.tiers.ProMax} · ${tierLabel("Power", true)} ${k.tiers.Power}`} valueStyle={{ fontSize: 14 }} /></Card></Col>
            </Row>

            <Card size="small" title={`${t("report.upgrades")}（${t("report.count", { n: data!.upgrades.length })}）`} style={{ marginBottom: 16 }}>
              <Table<ReportRecoRow> rowKey="email" size="small" columns={cols} dataSource={data!.upgrades}
                pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50] }}
                locale={{ emptyText: t("report.none") }} scroll={{ x: 700 }} />
            </Card>

            <Card size="small" title={`${t("report.downgrades")}（${t("report.count", { n: data!.downgrades.length })}）`}>
              <Table<ReportRecoRow> rowKey="email" size="small" columns={cols} dataSource={data!.downgrades}
                pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50] }}
                locale={{ emptyText: t("report.none") }} scroll={{ x: 700 }} />
            </Card>
          </>
        )}
      </Spin>
    </AppShell>
  );
}
