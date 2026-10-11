import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

export type Lang = "zh" | "en";

const STORAGE_KEY = "kirodash.lang";

/** 支持的语言与显示名（用于下拉框） */
export const LANGUAGES: { value: Lang; label: string }[] = [
  { value: "zh", label: "简体中文" },
  { value: "en", label: "English" },
];

/**
 * 订阅档位统一显示（Kiro 官方叫法 + 月费）。
 * 后端返回的原始 key 为 Pro / ProPlus / ProMax / Power；
 * 全站显示一律走 tierLabel()，避免各处叫法/大小写不一致。
 */
export const TIER_ORDER = ["Pro", "ProPlus", "ProMax", "Power"] as const;
export const TIER_LABELS: Record<string, string> = {
  Pro: "Pro",
  ProPlus: "Pro+",
  ProMax: "Pro Max",
  Power: "Power",
};
export const TIER_PRICE_USD: Record<string, number> = {
  Pro: 20,
  ProPlus: 40,
  ProMax: 100,
  Power: 200,
};

/** 档位显示名；withPrice 时追加月费，如 "Power ($200)"。未知 key 原样返回。 */
export function tierLabel(key: string, withPrice = false): string {
  const name = TIER_LABELS[key] ?? key;
  const price = TIER_PRICE_USD[key];
  return withPrice && price != null ? `${name} ($${price})` : name;
}

/**
 * 文案词典。key 为语义标识，value 为各语言文案。
 * 只覆盖静态 UI 文案；专有名词（部门名/人名）与 AI 回复不在此翻译。
 */
const DICT: Record<string, { zh: string; en: string }> = {
  // Header / 通用
  "app.title": { zh: "Kiro 使用效能看板", en: "Kiro Usage Dashboard" },
  "app.admin": { zh: "管理员", en: "Admin" },
  "app.logout": { zh: "退出登录", en: "Sign out" },
  "app.language": { zh: "语言", en: "Language" },
  "common.refresh": { zh: "刷新", en: "Refresh" },
  "common.loadFailed": { zh: "加载失败", en: "Failed to load" },
  "common.loading": { zh: "加载中…", en: "Loading…" },

  // 导航
  "nav.dashboard": { zh: "看板", en: "Dashboard" },
  "nav.report": { zh: "月度报告", en: "Monthly report" },
  "nav.modelConcentration": { zh: "模型集中度", en: "Model Concentration" },
  "nav.admins": { zh: "管理员管理", en: "Admins" },
  "nav.adminCenter": { zh: "管理中心", en: "Admin Center" },
  "nav.tierRequests": { zh: "调档申请", en: "Tier requests" },

  // 管理中心（左侧菜单）
  "adminCenter.users": { zh: "管理员管理", en: "Admins" },
  "adminCenter.multipliers": { zh: "费率维护", en: "Multipliers" },

  // 月度报告页
  "report.title": { zh: "月度使用报告", en: "Monthly usage report" },
  "report.downloadCsv": { zh: "下载 CSV", en: "Download CSV" },
  "report.upgrades": { zh: "建议升档（用量超出当前档位额度）", en: "Suggested upgrades (usage exceeds current tier)" },
  "report.downgrades": { zh: "建议降档（更低档位即可覆盖）", en: "Suggested downgrades (a lower tier covers usage)" },
  "report.col.name": { zh: "姓名 / 邮箱", en: "Name / Email" },
  "report.col.department": { zh: "部门", en: "Department" },
  "report.col.current": { zh: "当前档位", en: "Current tier" },
  "report.col.usage": { zh: "用量 / 额度", en: "Usage / Limit" },
  "report.col.rate": { zh: "使用率", en: "Usage rate" },
  "report.col.suggested": { zh: "建议档位", en: "Suggested tier" },
  "report.count": { zh: "共 {n} 人", en: "{n} users" },
  "report.none": { zh: "无", en: "None" },

  // 管理员管理页
  "admins.title": { zh: "管理员管理", en: "Admin management" },
  "admins.onboard": { zh: "Onboard 新管理员", en: "Onboard admin" },
  "admins.col.email": { zh: "邮箱", en: "Email" },
  "admins.col.status": { zh: "状态", en: "Status" },
  "admins.col.actions": { zh: "操作", en: "Actions" },
  "admins.status.active": { zh: "已启用", en: "Active" },
  "admins.status.pending": { zh: "待激活", en: "Pending" },
  "admins.status.disabled": { zh: "已禁用", en: "Disabled" },
  "admins.action.reset": { zh: "重置密码", en: "Reset password" },
  "admins.action.resend": { zh: "重发邀请", en: "Resend invite" },
  "admins.action.remove": { zh: "移除", en: "Remove" },
  "admins.onboard.emailPlaceholder": { zh: "新管理员邮箱", en: "New admin email" },
  "admins.onboard.hint": { zh: "将创建用户并加入 admins 组，Cognito 会自动发含临时密码的邀请邮件。", en: "Creates the user, adds to admins group; Cognito emails a temporary password automatically." },
  "admins.confirm.reset": { zh: "确认重置该管理员密码？", en: "Reset this admin's password?" },
  "admins.confirm.remove": { zh: "确认将该管理员移出 admins 组（撤销访问）？", en: "Remove this admin from the admins group (revoke access)?" },
  "admins.ok": { zh: "确定", en: "OK" },
  "admins.cancel": { zh: "取消", en: "Cancel" },
  "admins.done": { zh: "操作成功", en: "Done" },

  // 调档申请页（档位复用 tierLabel；状态键语言无关 R76）
  "tierReq.title": { zh: "调档申请", en: "Tier change requests" },
  "tierReq.col.requester": { zh: "申请人", en: "Requester" },
  "tierReq.search": { zh: "按邮箱搜索申请记录", en: "Search by email" },
  "tierReq.col.name": { zh: "姓名", en: "Name" },
  "tierReq.col.department": { zh: "部门", en: "Department" },
  "tierReq.col.target": { zh: "目标档位", en: "Target tier" },
  "tierReq.col.reason": { zh: "原因", en: "Reason" },
  "tierReq.col.status": { zh: "状态", en: "Status" },
  "tierReq.col.time": { zh: "申请时间", en: "Created at" },
  "tierReq.col.actions": { zh: "操作", en: "Actions" },
  "tierReq.col.note": { zh: "备注", en: "Note" },
  "tierReq.action.close": { zh: "关闭", en: "Close" },
  "tierReq.action.completed": { zh: "完成", en: "Complete" },
  "tierReq.action.rejected": { zh: "驳回", en: "Reject" },
  "tierReq.note.placeholder": { zh: "备注（可选）", en: "Note (optional)" },
  "tierReq.status.pending": { zh: "待处理", en: "Pending" },
  "tierReq.status.completed": { zh: "已完成", en: "Completed" },
  "tierReq.status.rejected": { zh: "已驳回", en: "Rejected" },
  "tierReq.status.superseded": { zh: "已失效", en: "Superseded" },

  // 调档申请页 — 右侧 MCP 配置指引
  "tierReq.mcp.title": { zh: "在 Kiro IDE 中配置调档申请", en: "Set up tier requests in Kiro IDE" },
  "tierReq.mcp.intro": {
    zh: "把下面的配置分发给用户，让他们在自己的 Kiro IDE 里添加远程 MCP server，即可用自然语言发起调档申请。无需在本机安装任何代码。",
    en: "Share the config below with users. They add this remote MCP server in their own Kiro IDE, then request tier changes in natural language. No local install required.",
  },
  "tierReq.mcp.step1": {
    zh: "1. 打开用户级配置文件 `~/.kiro/settings/mcp.json`（或工作区 `.kiro/settings/mcp.json`）。不确定位置 / Windows 用户，可按下图在 Kiro 侧边栏打开：点幽灵图标 → 展开底部 **MCP SERVERS** → 点右侧铅笔图标编辑 mcp.json。",
    en: "1. Open the user-level config `~/.kiro/settings/mcp.json` (or workspace `.kiro/settings/mcp.json`). Not sure where it is / on Windows? Use the Kiro sidebar as shown below: click the ghost icon → expand **MCP SERVERS** at the bottom → click the pencil icon on the right to edit mcp.json.",
  },
  "tierReq.mcp.imgAlt": { zh: "在 Kiro 侧边栏打开 mcp.json 的步骤示意图", en: "How to open mcp.json from the Kiro sidebar" },
  "tierReq.mcp.step2": {
    zh: "2. 把下面的 `kiro-tier-request` 块合并到 `mcpServers` 里（把 `X-Requester-Email` 改成用户本人邮箱）。",
    en: "2. Merge the `kiro-tier-request` block below into `mcpServers` (change `X-Requester-Email` to the user's own email).",
  },
  "tierReq.mcp.step3": {
    zh: "3. 保存后 Kiro 自动连接；在聊天里说「帮我申请升到 Pro Max」即可提交申请。",
    en: "3. Save; Kiro connects automatically. In chat say “request an upgrade to Pro Max” to submit.",
  },
  "tierReq.mcp.copy": { zh: "复制配置", en: "Copy config" },
  "tierReq.mcp.copied": { zh: "已复制", en: "Copied" },
  "tierReq.mcp.emailHint": {
    zh: "↑ 请把 `X-Requester-Email` 换成用户本人邮箱（标识申请人，每台机器配一次）。",
    en: "↑ Replace `X-Requester-Email` with the user's own email (identifies the requester; set once per machine).",
  },
  "tierReq.mcp.secWarn": {
    zh: "⚠ API key 为共享密钥，请通过可信渠道分发，勿公开。该密钥只挡随机公网滥用；申请人邮箱非验证身份，管理员审批为最终背书。",
    en: "⚠ The API key is a shared secret—distribute via a trusted channel, keep it private. It only blocks random public abuse; requester email is self-declared, admin approval is the backstop.",
  },

  // 模型倍率维护页
  "mult.title": { zh: "模型倍率维护", en: "Model multipliers" },
  "mult.intro": {
    zh: "每次模型调用按倍率折算 Credits。官方倍率来自 Kiro 官网，推测/未知值请管理员按实际情况编辑后保存。",
    en: "Each model call consumes credits scaled by its multiplier. Official values come from Kiro's site; review estimated/unknown values and save.",
  },
  "mult.col.model": { zh: "模型", en: "Model" },
  "mult.col.multiplier": { zh: "倍率", en: "Multiplier" },
  "mult.col.source": { zh: "来源", en: "Source" },
  "mult.source.official": { zh: "官方", en: "Official" },
  "mult.source.estimated": { zh: "推测", en: "Estimated" },
  "mult.source.edited": { zh: "已编辑", en: "Edited" },
  "mult.source.unknown": { zh: "未知", en: "Unknown" },
  "mult.estimatedHint": {
    zh: "无官方对应条目，当前为推测值，请核对后编辑保存。",
    en: "No official entry; this is an estimated value—please review and save.",
  },
  "mult.unknownHint": {
    zh: "数据中出现但无倍率，请为其设定倍率后保存。",
    en: "Seen in data but has no multiplier—please set one and save.",
  },
  "mult.unset": { zh: "未设定", en: "Unset" },
  "mult.save": { zh: "保存", en: "Save" },
  "mult.saved": { zh: "已保存", en: "Saved" },

  // 过滤器
  "filter.byPeriod": { zh: "按周期", en: "By period" },
  "filter.byRange": { zh: "按日期范围", en: "By date range" },
  "filter.selectPeriod": { zh: "选择数据周期", en: "Select period" },

  // KPI 卡片
  "kpi.registeredActive": { zh: "注册 / 活跃", en: "Registered / Active" },
  "kpi.registeredActive.tip": {
    zh: "注册总数=已同步用户；活跃=区间内有用量的用户",
    en: "Registered = synced users; Active = users with usage in range",
  },
  "kpi.activationRate": { zh: "激活率", en: "Activation rate" },
  "kpi.activationRate.tip": { zh: "活跃用户 / 注册总数", en: "Active users / Registered total" },
  "kpi.dailyActive": { zh: "日活（均/峰）", en: "Daily active (avg/peak)" },
  "kpi.dailyActive.tip": {
    zh: "区间内每日活跃用户的均值 / 峰值",
    en: "Average / peak of daily active users in range",
  },
  "kpi.unit.person": { zh: "人", en: "users" },
  "kpi.totalCredits": { zh: "总 Credits", en: "Total credits" },
  "kpi.overageUsers": { zh: "超额用户数", en: "Overage users" },
  "kpi.overageUsers.tip": {
    zh: "区间累计 credits 超过套餐额度的用户",
    en: "Users whose cumulative credits exceed their plan limit",
  },
  "kpi.estCost": { zh: "预估成本", en: "Est. cost" },
  "kpi.avgCredits": { zh: "人均 Credits", en: "Credits per active" },

  // 档位分布
  "tier.title": { zh: "订阅档位分布", en: "Subscription tier distribution" },
  "tier.unit": { zh: "人", en: "" },

  // 图表
  "chart.trend.title": { zh: "每日 Credits 消耗趋势", en: "Daily credits usage trend" },
  "chart.trend.credits": { zh: "Credits", en: "Credits" },
  "chart.trend.activeUsers": { zh: "活跃用户", en: "Active users" },
  "chart.models.title": { zh: "模型使用分布", en: "Model usage distribution" },
  "chart.dept.title": { zh: "各部门 Credits 对比 / 人均", en: "Credits by department / per capita" },
  "chart.dept.total": { zh: "总 Credits", en: "Total credits" },
  "chart.dept.avg": { zh: "人均 Credits", en: "Per-capita credits" },
  "chart.top.title": { zh: "Top {n} 用户", en: "Top {n} users" },

  // 模型集中度分析（模块 A）
  "conc.title": { zh: "模型集中度分析", en: "Model concentration" },
  "conc.caption": {
    zh: "统计口径=消息数占比（非成本占比）：某员工对所选模型的消息数合计占其个人全部模型消息数的比例。",
    en: "Metric = message-count share (not cost): the selected models' combined messages as a share of each user's total model messages.",
  },
  "conc.excludeZero": {
    zh: "分母=区间内有消息的员工（已排除 0 用量用户）。",
    en: "Denominator = users with messages in range (zero-usage users excluded).",
  },
  "conc.selectModels": { zh: "选择模型（可多选）", en: "Select models" },
  "conc.threshold": { zh: "集中度阈值：{pct}%", en: "Concentration threshold: {pct}%" },
  "conc.rate": { zh: "超阈员工占比", en: "Share above threshold" },
  "conc.counts": { zh: "超阈 / 总员工数", en: "Above threshold / total" },
  "conc.empty": { zh: "请选择一个或多个模型查看集中度分布", en: "Select one or more models to view the distribution" },
  "conc.hist.xAxis": { zh: "个人占比区间", en: "Per-user share range" },
  "conc.hist.yAxis": { zh: "员工数", en: "Users" },
  "conc.hist.users": { zh: "员工数", en: "Users" },
  "conc.noData": {
    zh: "所选模型在当前时间段无使用数据",
    en: "Selected models have no usage in this range",
  },
  // conc.table.title 已被 conc.table.overTitle / conc.table.bucketTitle 取代（保留无害）。
  "conc.table.title": { zh: "超阈人员明细", en: "Users above threshold" },
  "conc.table.empty": { zh: "当前阈值下无超阈人员", en: "No users above the current threshold" },
  "conc.col.name": { zh: "姓名", en: "Name" },
  "conc.col.department": { zh: "部门", en: "Department" },
  "conc.col.email": { zh: "邮箱", en: "Email" },
  "conc.col.targetMsgs": { zh: "所选模型消息数", en: "Selected-model messages" },
  "conc.col.totalMsgs": { zh: "总消息数", en: "Total messages" },
  "conc.col.ratio": { zh: "占比(%)", en: "Share (%)" },
  "conc.export": { zh: "导出 CSV", en: "Export CSV" },
  "conc.exportHint": {
    zh: "导出当前阈值下的超阈人员",
    en: "Export users above the current threshold",
  },
  "conc.table.overTitle": {
    zh: "超阈员工（占比 ≥ {pct}%）",
    en: "Employees above threshold (ratio ≥ {pct}%)",
  },
  "conc.table.bucketTitle": {
    zh: "占比 {from}–{to}% 的员工（共 {n} 人）",
    en: "Employees with ratio {from}–{to}% ({n} total)",
  },
  "conc.table.clearBucket": {
    zh: "返回超阈视图",
    en: "Back to threshold view",
  },
  "conc.hist.clickHint": {
    zh: "点击柱状图可按占比区间下钻人员明细",
    en: "Click a bar to drill down the member list by share range",
  },

  // 用户明细表
  "users.title": { zh: "用户明细", en: "User details" },
  "users.all": { zh: "全部", en: "All" },
  "users.search": { zh: "按姓名/邮箱搜索", en: "Search by name / email" },
  "users.download": { zh: "下载 CSV", en: "Download CSV" },
  "users.downloadHint": { zh: "导出当前部门/搜索范围内的全部用户（含零用量）", en: "Export all users in the current department / search scope (including zero usage)" },
  "users.downloadFail": { zh: "下载失败", en: "Download failed" },
  "users.col.rank": { zh: "排名", en: "Rank" },
  "users.col.name": { zh: "姓名 / 邮箱", en: "Name / Email" },
  "users.col.department": { zh: "部门", en: "Department" },
  "users.col.tier": { zh: "档位", en: "Tier" },
  "users.col.credits": { zh: "Credits", en: "Credits" },
  "users.col.limit": { zh: "额度", en: "Limit" },
  "users.col.balance": { zh: "余额", en: "Balance" },
  "users.col.usageRate": { zh: "使用率", en: "Usage rate" },
  "users.col.messages": { zh: "总消耗", en: "Messages" },
  "users.col.activeDays": { zh: "活跃天数", en: "Active days" },
  "users.col.status": { zh: "状态", en: "Status" },
  "users.total": { zh: "共 {n} 人", en: "{n} users" },

  // 用户模型使用下钻（抽屉）
  "userModels.title": { zh: "用户模型使用量", en: "User model usage" },
  "userModels.subtitle": { zh: "区间内各模型消息数", en: "Messages per model in range" },
  "userModels.messages": { zh: "消息数", en: "Messages" },
  "userModels.empty": { zh: "该用户在所选时间范围内无模型使用记录", en: "No model usage for this user in the selected range" },

  // 状态标签（后端返回 key，前端本地化）
  "status.overage": { zh: "超额", en: "Overage" },
  "status.near_limit": { zh: "接近上限", en: "Near limit" },
  "status.normal": { zh: "正常", en: "Normal" },
  "status.unused": { zh: "未使用", en: "Unused" },

  // ChatBox
  "chat.title": { zh: "自然语言问答", en: "Natural-language Q&A" },
  "chat.greeting": {
    zh: "你好，我是用量分析助手。可以问我关于 Credits、部门、超额、模型分布等问题。",
    en: "Hi, I'm your usage analytics assistant. Ask me about credits, departments, overage, model distribution, etc.",
  },
  "chat.placeholder": { zh: "输入用量相关问题…", en: "Ask a usage-related question…" },
  "chat.send": { zh: "发送", en: "Send" },
  "chat.noAnswer": { zh: "（无回答）", en: "(no answer)" },
  "chat.error": { zh: "出错了：", en: "Error: " },
  "chat.suggest.1": { zh: "本月哪个部门 Credits 消耗最高？", en: "Which department used the most credits this month?" },
  "chat.suggest.2": { zh: "谁触发了超额？", en: "Who triggered overage?" },
  "chat.suggest.3": { zh: "各档位分别有多少人？", en: "How many users are in each tier?" },
  "chat.suggest.4": { zh: "最近的 Credits 使用趋势如何？", en: "What's the recent credits usage trend?" },

  // 登录 / 回调
  "login.title": { zh: "Kiro 使用效能看板", en: "Kiro Usage Dashboard" },
  "login.subtitle": { zh: "仅限管理员访问，请使用企业账号登录。", en: "Admins only. Please sign in with your corporate account." },
  "login.button": { zh: "使用 Cognito 登录", en: "Sign in with Cognito" },
  "callback.failTitle": { zh: "登录失败", en: "Sign-in failed" },
  "callback.back": { zh: "返回登录", en: "Back to sign-in" },
  "callback.finishing": { zh: "正在完成登录…", en: "Finishing sign-in…" },
  "config.failed": { zh: "配置加载失败：", en: "Config load failed: " },
};

interface I18nCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
}

const Ctx = createContext<I18nCtx | null>(null);

function initialLang(): Lang {
  const saved = (typeof localStorage !== "undefined" && localStorage.getItem(STORAGE_KEY)) as Lang | null;
  return saved === "en" || saved === "zh" ? saved : "zh";
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem(STORAGE_KEY, l);
    } catch {
      /* ignore */
    }
  }, []);

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => {
      const entry = DICT[key];
      let s = entry ? entry[lang] : key;
      if (vars) {
        for (const [k, v] of Object.entries(vars)) {
          s = s.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
        }
      }
      return s;
    },
    [lang]
  );

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18nCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useI18n must be used within LanguageProvider");
  return c;
}
