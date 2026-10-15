import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";

/** ECharts 深色画布通用包装。 */
export function Chart({
  option,
  height = 300,
  onEvents,
  onChartReady,
}: {
  option: EChartsOption;
  height?: number;
  onEvents?: Record<string, (params: any) => void>;
  /** 拿到 ECharts 实例的回调（可选，向后兼容）。用于需要 zrender 级事件/坐标换算的图表。 */
  onChartReady?: (chart: any) => void;
}) {
  return (
    <ReactECharts
      option={{ backgroundColor: "transparent", textStyle: { color: "#cbd5e1" }, ...option }}
      style={{ height }}
      opts={{ renderer: "canvas" }}
      notMerge
      onEvents={onEvents}
      onChartReady={onChartReady}
    />
  );
}

/**
 * HTML 转义：ECharts 回调式 tooltip formatter 返回的是 HTML 字符串，
 * 插值前必须转义（用户名/部门等来自 Identity Center，不可信）。
 */
export const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
export const axisStyle = {
  axisLine: { lineStyle: { color: "#334155" } },
  splitLine: { lineStyle: { color: "#1e2a44" } },
  axisLabel: { color: "#94a3b8" },
};
