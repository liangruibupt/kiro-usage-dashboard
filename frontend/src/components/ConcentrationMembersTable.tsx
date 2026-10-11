import { useMemo } from "react";
import { Button, Card, Space, Table } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import type { ConcentrationBucket, ConcentrationMember, DateRange } from "../api/types";
import { useI18n } from "../i18n";
import { buildCsv, buildFilename, filterMembers, filterMembersByBucket } from "./concentrationExport";

/**
 * 超阈人员明细表格 + CSV 导出。
 * - 行集 = members 按当前阈值本地过滤（ratio>=thresholdPct/100），已降序。
 * - 阈值滑块变化时 thresholdPct 变化触发 useMemo 重算，实时更新，无需请求。
 * - 导出 CSV：前端纯本地生成（Blob + a[download]），带 UTF-8 BOM；文件名含模型/阈值/时间段。
 * - 纯逻辑（过滤 / CSV / 文件名）抽到 concentrationExport.ts，便于单测与复用。
 */
export function ConcentrationMembersTable({
  members,
  thresholdPct,
  models,
  range,
  loading,
  selectedBucket = null,
  bins,
  buckets,
  onClearBucket,
}: {
  members: ConcentrationMember[];
  thresholdPct: number;
  models: string[];
  range: DateRange;
  loading?: boolean;
  /** null=超阈视图；数字=桶下钻视图。 */
  selectedBucket?: number | null;
  /** = data.histogram.length，用于桶过滤口径与柱高一致。 */
  bins?: number;
  /** = data.histogram，用于标题/文件名显示桶区间 from/to。 */
  buckets?: ConcentrationBucket[];
  /** 清除下钻，返回超阈视图。 */
  onClearBucket?: () => void;
}) {
  const { t } = useI18n();

  const inBucketView = selectedBucket != null;

  const rows = useMemo(() => {
    if (inBucketView && bins) {
      return filterMembersByBucket(members, selectedBucket!, bins);
    }
    return filterMembers(members, thresholdPct);
  }, [members, thresholdPct, selectedBucket, bins, inBucketView]);

  const title = inBucketView
    ? t("conc.table.bucketTitle", {
        from: Math.round((buckets?.[selectedBucket!]?.from ?? 0) * 100),
        to: Math.round((buckets?.[selectedBucket!]?.to ?? 0) * 100),
        n: rows.length,
      })
    : t("conc.table.overTitle", { pct: thresholdPct });

  const columns: ColumnsType<ConcentrationMember> = [
    { title: t("conc.col.name"), dataIndex: "display_name" },
    { title: t("conc.col.department"), dataIndex: "department", width: 160 },
    { title: t("conc.col.email"), dataIndex: "email" },
    {
      title: t("conc.col.targetMsgs"),
      dataIndex: "target_messages",
      align: "right",
      width: 160,
    },
    {
      title: t("conc.col.totalMsgs"),
      dataIndex: "total_messages",
      align: "right",
      width: 130,
    },
    {
      title: t("conc.col.ratio"),
      dataIndex: "ratio",
      align: "right",
      width: 110,
      render: (v: number) => `${(v * 100).toFixed(1)}%`,
    },
  ];

  const exportCsv = () => {
    if (rows.length === 0) return;
    const header = [
      t("conc.col.name"),
      t("conc.col.department"),
      t("conc.col.email"),
      t("conc.col.targetMsgs"),
      t("conc.col.totalMsgs"),
      t("conc.col.ratio"),
    ];
    const text = buildCsv(header, rows);
    const blob = new Blob([text], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = buildFilename(
      models,
      thresholdPct,
      range,
      inBucketView ? (buckets?.[selectedBucket!] ?? null) : null
    );
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Card
      size="small"
      title={title}
      extra={
        <Space>
          {inBucketView && (
            <Button size="small" onClick={onClearBucket}>
              {t("conc.table.clearBucket")}
            </Button>
          )}
          <Button
            icon={<DownloadOutlined />}
            onClick={exportCsv}
            disabled={rows.length === 0}
            title={t("conc.exportHint")}
          >
            {t("conc.export")}
          </Button>
        </Space>
      }
    >
      <Table<ConcentrationMember>
        rowKey="email"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={rows}
        pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50] }}
        locale={{ emptyText: t("conc.table.empty") }}
        scroll={{ x: 700 }}
      />
    </Card>
  );
}
