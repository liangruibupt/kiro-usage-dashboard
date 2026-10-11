import type { EChartsOption } from "echarts";
import { Chart, axisStyle } from "./Chart";
import type { ConcentrationBucket } from "../../api/types";
import { useI18n } from "../../i18n";

/** 模型集中度直方图：x=个人占比区间（如 0–10%），y=员工数。 */
export function ConcentrationHistogram({ buckets }: { buckets: ConcentrationBucket[] }) {
  const { t } = useI18n();
  const labels = buckets.map(
    (b) => `${Math.round(b.from * 100)}–${Math.round(b.to * 100)}%`
  );
  const option: EChartsOption = {
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (p: any) => {
        const i = p[0].dataIndex;
        return `${labels[i]}<br/>${t("conc.hist.users")}: <b>${buckets[i].count}</b>`;
      },
    },
    grid: { left: 48, right: 24, top: 16, bottom: 40 },
    xAxis: {
      type: "category",
      data: labels,
      name: t("conc.hist.xAxis"),
      nameLocation: "middle",
      nameGap: 28,
      ...axisStyle,
      axisLabel: { color: "#94a3b8", rotate: 45 },
    },
    yAxis: {
      type: "value",
      name: t("conc.hist.yAxis"),
      minInterval: 1,
      ...axisStyle,
    },
    series: [
      {
        type: "bar",
        data: buckets.map((b) => b.count),
        itemStyle: { color: "#38bdf8", borderRadius: [4, 4, 0, 0] },
        barMaxWidth: 28,
      },
    ],
  };
  return <Chart option={option} height={300} />;
}
