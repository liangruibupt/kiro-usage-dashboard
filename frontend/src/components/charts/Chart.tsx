import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";

/** ECharts 深色画布通用包装。 */
export function Chart({
  option,
  height = 300,
  onEvents,
}: {
  option: EChartsOption;
  height?: number;
  onEvents?: Record<string, (params: any) => void>;
}) {
  return (
    <ReactECharts
      option={{ backgroundColor: "transparent", textStyle: { color: "#cbd5e1" }, ...option }}
      style={{ height }}
      opts={{ renderer: "canvas" }}
      notMerge
      onEvents={onEvents}
    />
  );
}

export const axisStyle = {
  axisLine: { lineStyle: { color: "#334155" } },
  splitLine: { lineStyle: { color: "#1e2a44" } },
  axisLabel: { color: "#94a3b8" },
};
