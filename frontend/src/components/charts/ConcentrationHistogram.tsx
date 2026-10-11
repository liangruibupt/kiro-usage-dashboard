import { useRef } from "react";
import type { EChartsOption } from "echarts";
import { Chart, axisStyle } from "./Chart";
import type { ConcentrationBucket } from "../../api/types";
import { useI18n } from "../../i18n";

/** 高亮/常态柱色；per-point itemStyle 控制选中桶。 */
const NORMAL_COLOR = "#38bdf8";
const SELECTED_COLOR = "#f59e0b";
/** 选中列整列高亮背景（覆盖 0 人列——0 人柱体高度为 0，靠 markArea 给出视觉反馈）。 */
const SELECTED_AREA = "rgba(245, 158, 11, 0.18)";

/**
 * 模型集中度直方图：x=个人占比区间（如 0–10%），y=员工数。
 * - selectedBucket：高亮哪个桶（null=无选中）。
 * - onBucketClick：点击某**列**（整列任意位置，含柱子上方空白、含 0 人列）回传其桶索引，
 *   由父级决定选中/取消语义。命中按点击的 x 像素反查类目列，与点击高度无关
 *   （靠坐标换算，不需要可见的列背景）。
 *
 * 命中实现（避免"必须精确点中细柱"）：
 *   不依赖 series click（只在柱体/背景上触发且 0 人列不可靠），改用 zrender 级 click：
 *   containPixel(grid 内) → convertFromPixel(xAxisIndex) 得最近类目 index → 范围校验 → 回调。
 *   zr handler 在 onChartReady 注册一次，但通过 ref 读取最新 buckets/回调，避免闭包捕获陈旧值
 *   （echarts-for-react 的 onChartReady 只触发一次）。
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

  // 最新值存入 ref：zr click handler 只注册一次（onChartReady），运行时读 ref 拿到当前
  // buckets 与回调，避免捕获陈旧闭包（切换模型/阈值后 buckets 变化时命中仍正确）。
  const bucketsRef = useRef(buckets);
  bucketsRef.current = buckets;
  const onClickRef = useRef(onBucketClick);
  onClickRef.current = onBucketClick;
  const boundRef = useRef(false);

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
        // 选中列整列高亮（从底到顶），覆盖柱高为 0 的列；silent 不拦截点击。
        markArea:
          selectedBucket != null && labels[selectedBucket]
            ? {
                silent: true,
                itemStyle: { color: SELECTED_AREA },
                data: [
                  [
                    { xAxis: labels[selectedBucket] },
                    { xAxis: labels[selectedBucket] },
                  ],
                ],
              }
            : undefined,
        ...(onBucketClick ? { cursor: "pointer" } : {}),
        barMaxWidth: 28,
      },
    ],
  };

  // zrender 级点击：点整列（任意高度，含阴影/0 人列）即按 x 像素反查类目 index。
  const handleReady = onBucketClick
    ? (chart: any) => {
        if (boundRef.current) return; // 实例内只绑一次
        boundRef.current = true;
        const zr = chart.getZr();
        zr.on("click", (e: any) => {
          const cb = onClickRef.current;
          if (!cb) return;
          const x = e.offsetX;
          const y = e.offsetY;
          // 点击必须落在绘图区（grid）内，否则忽略（坐标轴标签/图外空白不触发）。
          if (!chart.containPixel({ gridIndex: 0 }, [x, y])) return;
          // 按 x 像素反查类目轴最近 index（与 y 无关）。
          const idx = chart.convertFromPixel({ xAxisIndex: 0 }, x);
          if (!Number.isFinite(idx)) return;
          const i = Math.round(idx);
          if (i < 0 || i >= bucketsRef.current.length) return;
          cb(i);
        });
      }
    : undefined;

  return <Chart option={option} height={300} onChartReady={handleReady} />;
}
