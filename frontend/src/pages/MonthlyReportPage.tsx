import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Col, Row, Slider, Spin, Statistic, Table, Tag } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { Dayjs } from "dayjs";
import { api, ApiAuthError } from "../api/client";
import { login } from "../auth/auth";
import type { FiltersResponse, ReportResponse, ReportUserRow } from "../api/types";
import { FilterBar, defaultRange, toDateRange, type FilterMode } from "../components/FilterBar";
import { AppShell } from "../components/AppShell";
import { csvCell } from "../components/concentrationExport";
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
  // 使用率阈值滑块（本地过滤，拖动零请求）：升档 80 / 降档 40 / 关停 0。
  const [upgradePct, setUpgradePct] = useState(80);
  const [downgradePct, setDowngradePct] = useState(40);
  const [shutdownPct, setShutdownPct] = useState(0);

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

  // 使用率（百分比）；usage_limit=0 时后端 usage_rate=null，归一为 0 参与比较。
  const rate = (r: ReportUserRow) => (r.usage_rate ?? 0) * 100;

  // 本地阈值过滤（复刻模型集中度页本地过滤，滑块拖动零请求）：
  const upgradeRows = useMemo(
    () => (data?.users ?? []).filter((r) => rate(r) >= upgradePct && r.suggested_up != null),
    [data, upgradePct]
  );
  const downgradeRows = useMemo(
    () =>
      (data?.users ?? []).filter(
        (r) => rate(r) < downgradePct && (r.credits_used || 0) > 0 && r.suggested_down != null
      ),
    [data, downgradePct]
  );
  const shutdownRows = useMemo(
    // usage_limit>0 门：排除额度缺失（usage_rate=null）的异常行，不误列为建议关停。
    () => (data?.users ?? []).filter((r) => rate(r) <= shutdownPct && (r.usage_limit || 0) > 0),
    [data, shutdownPct]
  );

  // 五个共用列：姓名/邮箱、部门、当前档位、用量/额度、使用率。
  const baseCols: ColumnsType<ReportUserRow> = [
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
      render: (v: number | null) => `${((v ?? 0) * 100).toFixed(1)}%`,
    },
  ];

  // 建议档位列工厂（升/降表共用，key 决定读哪个字段）；?? "" 收窄 string|null。
  const tierCol = (key: "suggested_up" | "suggested_down"): ColumnsType<ReportUserRow>[number] => ({
    title: t("report.col.suggested"),
    key,
    width: 110,
    render: (_v, r) => <Tag color="blue">{tierLabel(r[key] ?? "", true)}</Tag>,
  });

  const upgradeCols = [...baseCols, tierCol("suggested_up")];
  const downgradeCols = [...baseCols, tierCol("suggested_down")];
  const shutdownCols: ColumnsType<ReportUserRow> = [
    ...baseCols,
    {
      title: t("report.col.suggest"),
      key: "suggest",
      width: 140,
      render: () => <Tag color="red">{t("report.col.suggestShutdown")}</Tag>,
    },
  ];

  const downloadCsv = () => {
    if (!data) return;
    const header = [
      "type", "name", "email", "department", "current_tier",
      "credits_used", "usage_limit", "usage_rate", "suggested",
    ];
    const lines = [header.map(csvCell).join(",")];
    const push = (kind: string, rows: ReportUserRow[], suggestedOf: (r: ReportUserRow) => string) => {
      rows.forEach((r) => {
        lines.push(
          [
            csvCell(kind),
            csvCell(r.display_name || ""),
            csvCell(r.email),
            csvCell(r.department || ""),
            csvCell(tierLabel(r.subscription_tier)),
            csvCell(r.credits_used),
            csvCell(r.usage_limit),
            csvCell(((r.usage_rate ?? 0) * 100).toFixed(1) + "%"),
            csvCell(suggestedOf(r)),
          ].join(",")
        );
      });
    };
    push("upgrade", upgradeRows, (r) => tierLabel(r.suggested_up ?? ""));
    push("downgrade", downgradeRows, (r) => tierLabel(r.suggested_down ?? ""));
    push("shutdown", shutdownRows, () => t("report.col.suggestShutdown"));
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

            <Card size="small" title={`${t("report.upgrades")}（${t("report.count", { n: upgradeRows.length })}）`} style={{ marginBottom: 16 }}>
              <div style={{ marginBottom: 8, color: "#94a3b8" }}>{t("report.threshold.upgrade", { pct: upgradePct })}</div>
              <Slider min={0} max={100} value={upgradePct} onChange={setUpgradePct} tooltip={{ formatter: (v) => `${v}%` }} />
              <Table<ReportUserRow> rowKey="email" size="small" columns={upgradeCols} dataSource={upgradeRows}
                pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50] }}
                locale={{ emptyText: t("report.none") }} scroll={{ x: 700 }} />
            </Card>

            <Card size="small" title={`${t("report.downgrades")}（${t("report.count", { n: downgradeRows.length })}）`} style={{ marginBottom: 16 }}>
              <div style={{ marginBottom: 8, color: "#94a3b8" }}>{t("report.threshold.downgrade", { pct: downgradePct })}</div>
              <Slider min={0} max={100} value={downgradePct} onChange={setDowngradePct} tooltip={{ formatter: (v) => `${v}%` }} />
              <Table<ReportUserRow> rowKey="email" size="small" columns={downgradeCols} dataSource={downgradeRows}
                pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50] }}
                locale={{ emptyText: t("report.none") }} scroll={{ x: 700 }} />
            </Card>

            <Card size="small" title={`${t("report.shutdown")}（${t("report.count", { n: shutdownRows.length })}）`}>
              <div style={{ marginBottom: 8, color: "#94a3b8" }}>{t("report.threshold.shutdown", { pct: shutdownPct })}</div>
              <Slider min={0} max={100} value={shutdownPct} onChange={setShutdownPct} tooltip={{ formatter: (v) => `${v}%` }} />
              <Table<ReportUserRow> rowKey="email" size="small" columns={shutdownCols} dataSource={shutdownRows}
                pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50] }}
                locale={{ emptyText: t("report.none") }} scroll={{ x: 700 }} />
            </Card>
          </>
        )}
      </Spin>
    </AppShell>
  );
}
