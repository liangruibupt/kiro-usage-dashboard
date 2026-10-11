import { useEffect, useMemo, useState } from "react";
import { Alert, Card, Col, Empty, Row, Select, Slider, Spin, Statistic } from "antd";

import { api, ApiAuthError } from "../api/client";
import { login } from "../auth/auth";
import type { DateRange, ModelConcentrationResponse } from "../api/types";
import { ConcentrationHistogram } from "./charts/ConcentrationHistogram";
import { useI18n } from "../i18n";

/** 阈值滑块默认值（百分比）。 */
const DEFAULT_THRESHOLD_PCT = 50;
/** 默认预选倍率阈值：multiplier>=2.0 的模型。 */
const PRESELECT_MULTIPLIER = 2.0;

/**
 * 模型集中度分析卡片（模块 A）。
 * - Select 多选模型 + Slider 阈值（0–100%）。
 * - 模型选项来自 /api/model-multipliers 的全部模型（当前 21 个），默认预选 multiplier>=2.0。
 * - models/range 变化才请求 api.modelConcentration；slider 变化用返回的 ratios 本地重算。
 * - 选到无使用记录的模型占比为 0，不是 bug；全部不在白名单时后端 400，走中性提示。
 * - 统计口径=消息数占比（非成本占比），已排除 0 用量用户。
 * - 通过 onData/onThresholdPct 回调把数据与当前阈值暴露给页面（供人员表格共享）。
 */
export function ModelConcentrationCard({
  range,
  onData,
  onThresholdPct,
  onLoading,
  selectedBucket = null,
  onBucketClick,
}: {
  range: DateRange;
  onData?: (res: ModelConcentrationResponse | null) => void;
  onThresholdPct?: (pct: number) => void;
  onLoading?: (loading: boolean) => void;
  /** 高亮哪个桶，null=无（由页面注入）。 */
  selectedBucket?: number | null;
  /** 点击某桶回传原始索引（由页面做选中/取消判定）。 */
  onBucketClick?: (index: number) => void;
}) {
  const { t } = useI18n();
  const [selected, setSelected] = useState<string[]>([]);
  const [modelOptions, setModelOptions] = useState<{ label: string; value: string }[]>([]);
  const [preselectDone, setPreselectDone] = useState(false);
  const [thresholdPct, setThresholdPct] = useState<number>(DEFAULT_THRESHOLD_PCT);
  const [data, setData] = useState<ModelConcentrationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // 默认预选：加载全部模型倍率，选项=全部模型，预选 multiplier>=2.0。
  useEffect(() => {
    if (preselectDone) return;
    let active = true;
    api
      .modelMultipliers()
      .then((res) => {
        if (!active) return;
        // endpoint 已按 model 升序返回，直接映射为选项。
        setModelOptions(res.items.map((it) => ({ label: it.model, value: it.model })));
        const pre = res.items
          .filter((it) => it.multiplier != null && it.multiplier >= PRESELECT_MULTIPLIER)
          .map((it) => it.model);
        setSelected(pre);
        setPreselectDone(true);
      })
      .catch((e) => {
        // 倍率拉取失败不阻塞卡片：标记完成，留空选择由用户手动选。
        if (!active) return;
        setPreselectDone(true);
        if (e instanceof ApiAuthError) {
          login();
          return;
        }
        setError(String(e));
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preselectDone]);

  // models/range 变化才请求（slider 不触发）。
  useEffect(() => {
    if (!preselectDone) return;
    if (selected.length === 0) {
      // 清空选择：同步复位本地 loading（否则请求进行中清空会留下永久 spinner，
      // 因为旧 effect 的 cleanup 让其 finally 不再复位），并清理上一次的 notice/error。
      setLoading(false);
      setError(null);
      setNotice(null);
      setData(null);
      onData?.(null);
      onLoading?.(false);
      return;
    }
    let active = true;
    setLoading(true);
    onLoading?.(true);
    setError(null);
    // 新请求发起即让父级旧数据失效，避免表格/导出沿用上一次范围的结果。
    setData(null);
    onData?.(null);
    api
      .modelConcentration(range, selected, DEFAULT_THRESHOLD_PCT / 100)
      .then((res) => {
        if (!active) return;
        setData(res);
        setError(null);
        setNotice(null);
        onData?.(res);
      })
      .catch((e) => {
        if (!active) return;
        if (e instanceof ApiAuthError) {
          login();
          return;
        }
        const msg = String(e);
        if (msg.includes("no valid model selected")) {
          // 所选模型全部不在 Athena 白名单：中性提示，而非错误。
          setData(null);
          setError(null);
          setNotice(t("conc.noData"));
          onData?.(null);
        } else {
          // 其它失败：清空数据与父级响应，表格隐藏、导出禁用，不保留旧范围结果。
          setNotice(null);
          setError(msg);
          setData(null);
          onData?.(null);
        }
      })
      .finally(() => {
        if (!active) return;
        setLoading(false);
        onLoading?.(false);
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range, selected, preselectDone]);

  const threshold = thresholdPct / 100;

  const handleThresholdChange = (pct: number) => {
    setThresholdPct(pct);
    onThresholdPct?.(pct);
  };

  // slider 变化时用返回的 ratios 本地重算（ratio>=threshold 含等号），不重新请求。
  const stats = useMemo(() => {
    if (!data) return { total: 0, concentrated: 0, rate: 0 };
    const total = data.ratios.length;
    const concentrated = data.ratios.filter((x) => x >= threshold).length;
    return { total, concentrated, rate: total ? concentrated / total : 0 };
  }, [data, threshold]);

  return (
    <Card size="small" title={t("conc.title")}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message={t("conc.caption")}
        description={t("conc.excludeZero")}
      />
      <Row gutter={[16, 16]} align="middle">
        <Col xs={24} md={12}>
          <div style={{ marginBottom: 4, color: "#94a3b8" }}>{t("conc.selectModels")}</div>
          <Select
            mode="multiple"
            allowClear
            style={{ width: "100%" }}
            placeholder={t("conc.selectModels")}
            value={selected}
            onChange={setSelected}
            options={modelOptions}
          />
        </Col>
        <Col xs={24} md={12}>
          <div style={{ marginBottom: 4, color: "#94a3b8" }}>
            {t("conc.threshold", { pct: thresholdPct })}
          </div>
          <Slider
            min={0}
            max={100}
            value={thresholdPct}
            onChange={handleThresholdChange}
            tooltip={{ formatter: (v) => `${v}%` }}
          />
        </Col>
      </Row>

      {notice && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message={notice} />
      )}

      {error && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message={t("common.loadFailed")}
          description={error}
        />
      )}

      <Spin spinning={loading}>
        {selected.length === 0 ? (
          <Empty description={t("conc.empty")} style={{ marginTop: 24 }} />
        ) : data ? (
          <>
            <Row gutter={[16, 16]} style={{ marginTop: 8, marginBottom: 8 }}>
              <Col xs={12} md={8}>
                <Statistic
                  title={t("conc.rate")}
                  value={(stats.rate * 100).toFixed(1)}
                  suffix="%"
                />
              </Col>
              <Col xs={12} md={8}>
                <Statistic
                  title={t("conc.counts")}
                  value={stats.concentrated}
                  suffix={`/ ${stats.total}`}
                />
              </Col>
            </Row>
            <ConcentrationHistogram
              buckets={data.histogram}
              selectedBucket={selectedBucket}
              onBucketClick={onBucketClick}
            />
            {onBucketClick && (
              <div style={{ color: "#94a3b8", marginTop: 8 }}>{t("conc.hist.clickHint")}</div>
            )}
          </>
        ) : (
          <Empty description={t("conc.empty")} style={{ marginTop: 24 }} />
        )}
      </Spin>
    </Card>
  );
}
