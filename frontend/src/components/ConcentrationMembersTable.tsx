import { useMemo } from "react";
import { Button, Card, Table } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import type { ConcentrationMember, DateRange } from "../api/types";
import { useI18n } from "../i18n";
import { buildCsv, buildFilename, filterMembers } from "./concentrationExport";

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
}: {
  members: ConcentrationMember[];
  thresholdPct: number;
  models: string[];
  range: DateRange;
  loading?: boolean;
}) {
  const { t } = useI18n();

  const rows = useMemo(
    () => filterMembers(members, thresholdPct),
    [members, thresholdPct]
  );

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
    a.download = buildFilename(models, thresholdPct, range);
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Card
      size="small"
      title={t("conc.table.title")}
      extra={
        <Button
          icon={<DownloadOutlined />}
          onClick={exportCsv}
          disabled={rows.length === 0}
          title={t("conc.exportHint")}
        >
          {t("conc.export")}
        </Button>
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
