import { useEffect, useMemo, useState } from "react";
import { Space } from "antd";
import type { Dayjs } from "dayjs";

import { api } from "../api/client";
import type { FiltersResponse, ModelConcentrationResponse } from "../api/types";
import { FilterBar, defaultRange, toDateRange, type FilterMode } from "../components/FilterBar";
import { AppShell } from "../components/AppShell";
import { ModelConcentrationCard } from "../components/ModelConcentrationCard";
import { ConcentrationMembersTable } from "../components/ConcentrationMembersTable";

/**
 * 模型集中度独立页面。
 * - 自带 FilterBar（period / range 两模式）得到页面级 dateRange。
 * - 卡片自管请求，通过 onData/onThresholdPct/onLoading 把最近响应、当前阈值与加载态回传给页面。
 * - 新请求发起或失败时卡片会 onData(null) 让父级响应失效，表格随之清空、导出禁用，不保留旧范围结果。
 * - 页面把 members + 当前阈值转交人员表格（本地阈值过滤 + CSV 导出）。
 */
export function ModelConcentrationPage() {
  const [mode, setMode] = useState<FilterMode>("range");
  const [period, setPeriod] = useState<string | undefined>(undefined);
  const [periods, setPeriods] = useState<string[]>([]);
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(defaultRange());
  const [tick, setTick] = useState(0);

  const [res, setRes] = useState<ModelConcentrationResponse | null>(null);
  const [thresholdPct, setThresholdPct] = useState(50);
  const [loading, setLoading] = useState(false);
  const [selectedBucket, setSelectedBucket] = useState<number | null>(null); // null=未下钻

  const dateRange = useMemo(() => toDateRange(mode, period, range), [mode, period, range, tick]);

  // 分布改变时清除陈旧选中：models/date-range 经 res 新对象标识生效（卡片每次请求先 onData(null)
  // 再 onData(newRes)），threshold 经 thresholdPct 生效。再次点同柱取消的路径不触碰 res/thresholdPct，
  // 故不会被此 effect 误复位。
  useEffect(() => {
    setSelectedBucket(null);
  }, [res, thresholdPct]);

  // 再次点击同一柱=取消；histogram 只回传原始索引，页面在此决定选中/取消语义。
  const handleBucketClick = (index: number) =>
    setSelectedBucket((cur) => (cur === index ? null : index));

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AppShell active="modelConcentration">
      <div style={{ marginBottom: 16 }}>
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
      </div>

      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <ModelConcentrationCard
          range={dateRange}
          onData={setRes}
          onThresholdPct={setThresholdPct}
          onLoading={setLoading}
          selectedBucket={selectedBucket}
          onBucketClick={handleBucketClick}
        />
        <ConcentrationMembersTable
          members={res?.members ?? []}
          thresholdPct={thresholdPct}
          models={res?.models ?? []}
          range={dateRange}
          loading={loading}
          selectedBucket={selectedBucket}
          // bins 必须是后端桶数 res.histogram.length 原样传入，表格不得重算；
          // 这是 filterMembersByBucket(members,i,bins).length === histogram[i].count 成立的前提。
          bins={res?.histogram.length ?? 0}
          buckets={res?.histogram ?? []}
          onClearBucket={() => setSelectedBucket(null)}
        />
      </Space>
    </AppShell>
  );
}
