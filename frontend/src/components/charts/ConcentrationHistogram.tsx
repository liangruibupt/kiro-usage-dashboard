import type { EChartsOption } from "echarts";
import { Chart, axisStyle } from "./Chart";
import type { ConcentrationBucket } from "../../api/types";
import { useI18n } from "../../i18n";

/** 高亮/常态柱色；per-point itemStyle 控制选中桶。 */
const NORMAL_COLOR = "#38bdf8";
const SELECTED_COLOR = "#f59e0b";

/**
 * 模型集中度直方图：x=个人占比区间（如 0–10%），y=员工数。
 * - selectedBucket：高亮哪个桶（null=无选中）。
 * - onBucketClick：点击某柱回传其原始索引（= 桶索引，series[0].data 与 buckets 一一对应），
 *   由父级决定选中/取消语义。
 */
export function ConcentrationHistogram({
  buckets,
  selectedBucket = null,
  onBucketClick,
}: {
  buckets: ConcentrationBucket[];
  selectedBucket?: number | null;
  onBucketClick?: (index: number) => void;
}) {
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
        data: buckets.map((b, i) => ({
          value: b.count,
          itemStyle:
            i === selectedBucket
              ? {
                  color: SELECTED_COLOR,
                  borderColor: "#fde68a",
                  borderWidth: 2,
                  borderRadius: [4, 4, 0, 0],
                }
              : { color: NORMAL_COLOR, borderRadius: [4, 4, 0, 0] },
        })),
        ...(onBucketClick ? { cursor: "pointer" } : {}),
        barMaxWidth: 28,
      },
    ],
  };

  // 仅在父级提供 onBucketClick 时才接事件；非数字 dataIndex（空白处点击）忽略。
  const onEvents = onBucketClick
    ? {
        click: (p: any) => {
          if (typeof p?.dataIndex === "number") onBucketClick(p.dataIndex);
        },
      }
    : undefined;

  return <Chart option={option} height={300} onEvents={onEvents} />;
}
