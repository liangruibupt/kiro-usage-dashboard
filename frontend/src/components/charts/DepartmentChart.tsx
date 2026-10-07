import type { EChartsOption } from "echarts";
import { Card } from "antd";
import { Chart, axisStyle } from "./Chart";
import type { DepartmentRow } from "../../api/types";
import { useI18n } from "../../i18n";

/** 千分位格式化（用于柱端标签） */
const fmt = (n: number) => Math.round(n).toLocaleString("en-US");

/**
 * 各部门 Credits 消耗对比 + 人均（横向柱，R14）。
 * 部门名已在数据层中文化（identity-sync 从 Identity Center group Description 提取），
 * 前端直接展示，不再硬编码映射。
 */
export function DepartmentChart({ departments }: { departments: DepartmentRow[] }) {
  const { t } = useI18n();
  const totalLabel = t("chart.dept.total");
  const avgLabel = t("chart.dept.avg");
  // 按总 credits 升序，横向柱底部在上视觉更自然
  const rows = [...departments].sort((a, b) => a.total_credits - b.total_credits);
  const names = rows.map((r) => r.department);
  const option: EChartsOption = {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: { data: [totalLabel, avgLabel], textStyle: { color: "#94a3b8" }, top: 0 },
    // 右侧留更多空间，避免柱端数字被裁剪
    grid: { left: 150, right: 90, top: 36, bottom: 30 },
    xAxis: { type: "value", ...axisStyle },
    yAxis: { type: "category", data: names, ...axisStyle },
    series: [
      {
        name: totalLabel,
        type: "bar",
        data: rows.map((r) => r.total_credits),
        itemStyle: { color: "#3b82f6", borderRadius: [0, 4, 4, 0] },
        barMaxWidth: 16,
        label: {
          show: true,
          position: "right",
          color: "#e2e8f0",
          fontSize: 11,
          formatter: (p: any) => fmt(p.value as number),
        },
      },
      {
        name: avgLabel,
        type: "bar",
        data: rows.map((r) => r.avg_credits),
        itemStyle: { color: "#22c55e", borderRadius: [0, 4, 4, 0] },
        barMaxWidth: 16,
        label: {
          show: true,
          position: "right",
          color: "#e2e8f0",
          fontSize: 11,
          formatter: (p: any) => fmt(p.value as number),
        },
      },
    ],
  };
  return (
    <Card size="small" title={t("chart.dept.title")}>
      <Chart option={option} height={340} />
    </Card>
  );
}
