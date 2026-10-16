/**
 * 模型集中度人员明细的纯函数：阈值过滤、CSV 组装、文件名编码。
 * 抽成独立模块便于单测与复用；不含 React / DOM 依赖。
 * 口径标注：行集=members 按 ratio>=threshold 本地过滤，已按 ratio 降序，保持原顺序。
 */
import type { ConcentrationMember, DateRange } from "../api/types";

/**
 * 中和 CSV 公式注入：以 = + - @ 或前导制表符/回车开头的单元格，
 * 电子表格（Excel 等）可能解释为公式。先补一个前导单引号打断公式解析，
 * 再交给引号转义。制表符(\t)、回车(\r) 也是已知的公式触发前缀，一并覆盖。
 *
 * 纯函数用例（仓库无前端测试框架，用例以规格形式钉在此处，供评审核对）：
 *   neutralizeFormula("=HYPERLINK(1,2)") === "'=HYPERLINK(1,2)"
 *   neutralizeFormula("+1+1")            === "'+1+1"
 *   neutralizeFormula("-2")              === "'-2"
 *   neutralizeFormula("@SUM(A1)")        === "'@SUM(A1)"
 *   neutralizeFormula("\t=1")            === "'\t=1"   // 前导制表符
 *   neutralizeFormula("\r=1")            === "'\r=1"   // 前导回车
 *   neutralizeFormula("张伟")             === "张伟"      // 普通姓名不变
 *   neutralizeFormula("a@example.com")         === "a@example.com"  // @ 非首字符不触发
 *   neutralizeFormula("")                === ""         // 空串不加前缀
 * 经 csvCell 后，带逗号/引号/换行/回车的中和结果仍会被正常引号包裹，例如：
 *   csvCell("=1,2")   === "\"'=1,2\""（公式中和 + 逗号引号包裹）
 *   csvCell("\r=1+1") === "\"'\r=1+1\""（公式中和 + 回车引号包裹，\r 不拆记录）
 *   csvCell("张伟\r=HYPERLINK(1)") === "\"张伟\r=HYPERLINK(1)\""（内部回车整体引号包裹，
 *                                        不另起记录，回车后的 = 不落在新单元格开头）
 */
export function neutralizeFormula(s: string): string {
  if (s.length > 0 && /^[=+\-@\t\r]/.test(s)) {
    return `'${s}`;
  }
  return s;
}

/**
 * CSV 单元格编码：先中和公式前缀（见 neutralizeFormula），
 * 再做标准引号转义——含逗号/双引号/换行(\n)/回车(\r)时用双引号包裹并把内部 " 转成 ""。
 * 回车必须纳入引号判断：否则字段里的 \r 会把一个单元格拆成两条 CSV 记录，
 * 使回车后的 = + - @ 公式前缀重新落在新记录单元格开头，绕过只看字段首字符的公式中和。
 */
export function csvCell(v: string | number): string {
  const s = neutralizeFormula(String(v));
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

/** 按阈值（百分比）过滤成员；members 已降序，过滤保持原顺序。 */
export function filterMembers(
  members: ConcentrationMember[],
  thresholdPct: number
): ConcentrationMember[] {
  const th = thresholdPct / 100;
  return members.filter((m) => m.ratio >= th);
}

/**
 * 把个人占比 ratio ∈ [0,1] 映射到直方图桶索引，复刻后端
 * idx = min(bins-1, int(ratio*bins))：ratio==1.0 落到最后一桶（[0.9,1.0] 闭区间），
 * 其余桶为左闭右开 [from, to)。bins = 直方图桶数（data.histogram.length）。
 * ratio 越界（<0 或 >1，正常不会发生）同样夹到首/末桶，保证与柱高一致。
 *
 * Math.floor 对非负 ratio ∈ [0,1] 等价于 Python int()（向零截断），
 * 且后端桶算用的是未四舍五入的 member.ratio（直方图的 round 仅用于 from/to 显示，
 * 不参与下钻），故前端与后端桶归属逐一致。
 *
 * 纯函数用例（仓库无前端测试框架，用例以规格形式钉在此处，供评审核对）：
 *   bucketIndexOf(0.0, 10)  === 0
 *   bucketIndexOf(0.05, 10) === 0
 *   bucketIndexOf(0.1, 10)  === 1
 *   bucketIndexOf(0.3, 10)  === 3
 *   bucketIndexOf(0.95, 10) === 9
 *   bucketIndexOf(1.0, 10)  === 9   // 末桶夹取（min(bins-1, …)），与后端一致
 */
export function bucketIndexOf(ratio: number, bins: number): number {
  if (bins <= 0) return 0;
  const idx = Math.floor(ratio * bins);
  if (idx < 0) return 0;
  return Math.min(bins - 1, idx);
}

/**
 * 取某个桶的成员；bins 必须用 data.histogram.length 传入，口径与柱高一致。
 * Array.filter 保序，故桶视图沿用后端 ratio 降序。
 *
 * 核心不变量：filterMembersByBucket(members, i, bins).length === data.histogram[i].count，
 * 当且仅当 bins === data.histogram.length 原样传入时成立。
 * 以后端 test_api.py::test_model_concentration_ok 的夹具锚定（ratios [0.9,0.6,0.2,0.0]，bins 10
 * -> 分别落到桶 9/6/2/0，每桶 count 1）：
 *   filterMembersByBucket(fixture, 9, 10).length === 1  (ratio 0.9)
 *   filterMembersByBucket(fixture, 6, 10).length === 1  (ratio 0.6)
 *   filterMembersByBucket(fixture, 2, 10).length === 1  (ratio 0.2)
 *   filterMembersByBucket(fixture, 0, 10).length === 1  (ratio 0.0)
 */
export function filterMembersByBucket(
  members: ConcentrationMember[],
  bucketIndex: number,
  bins: number
): ConcentrationMember[] {
  return members.filter((m) => bucketIndexOf(m.ratio, bins) === bucketIndex);
}

/** 清洗文件名里的非法字符（/ \ : * ? " < > | 及空白）。 */
export function sanitizeFilename(s: string): string {
  return s.replace(/[/\\:*?"<>|\s]+/g, "_");
}

/**
 * 组装 CSV 文件名：含模型 / 阈值 / 时间段，采用语言无关编码（无自然语言字面量）。
 * - 模型：全选为空时用 `allmodels`；多于 3 个用 `m1_m2_m3_plusN`（N=剩余数量）。
 * - 视图：默认（超阈）为 `th<整数百分比>`；桶下钻视图为 `bucket<from%>-<to%>`。
 * - 时间段：`<from>_<to>`（去连字符）/ `to<to>`（仅结束）/ `<period>` / `allrange`。
 * 所有片段均为稳定 ASCII 编码，不随 UI 语言变化，故不需要走 DICT。
 *
 * 纯函数用例（供评审核对）：
 *   buildFilename(models, 50, range) 含 `th50`（无 bucket 参数时与旧行为逐字节一致）
 *   buildFilename(models, 50, range, {from:0.9, to:1.0}) 含 `bucket90-100`
 */
export function buildFilename(
  models: string[],
  thresholdPct: number,
  range: DateRange,
  bucket?: { from: number; to: number } | null
): string {
  let modelPart = "allmodels";
  if (models.length > 0) {
    modelPart =
      models.length > 3
        ? `${models.slice(0, 3).join("_")}_plus${models.length - 3}`
        : models.join("_");
  }

  let rangePart = "allrange";
  if (range.from && range.to) {
    rangePart = `${range.from}_${range.to}`.replace(/-/g, "");
  } else if (range.to) {
    rangePart = `to${range.to.replace(/-/g, "")}`;
  } else if (range.period) {
    rangePart = range.period.replace(/-/g, "");
  }

  const viewPart = bucket
    ? `bucket${Math.round(bucket.from * 100)}-${Math.round(bucket.to * 100)}`
    : `th${Math.round(thresholdPct)}`;

  const raw = `kiro-model-concentration-${modelPart}-${viewPart}-${rangePart}.csv`;
  return sanitizeFilename(raw);
}

/**
 * 组装 CSV 文本（含 UTF-8 BOM）。
 * @param headers 表头（随当前 UI 语言传入，由调用方用 DICT 翻译）
 * @param rows 已过滤、已排序的成员行
 */
export function buildCsv(headers: string[], rows: ConcentrationMember[]): string {
  const lines = [headers.map(csvCell).join(",")];
  rows.forEach((m) => {
    lines.push(
      [
        csvCell(m.display_name),
        csvCell(m.department),
        csvCell(m.email),
        csvCell(m.target_messages),
        csvCell(m.total_messages),
        csvCell((m.ratio * 100).toFixed(1)),
      ].join(",")
    );
  });
  // \ufeff = UTF-8 BOM，确保 Excel 正确识别中文（与 users_export 一致）。
  return "\ufeff" + lines.join("\n");
}
