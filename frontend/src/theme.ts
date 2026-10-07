import { theme, type ThemeConfig } from "antd";

/** 深色主题（贴近参考 UI）。主色用青蓝，强调用于图表与进度条。 */
export const darkTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: "#3b82f6",
    colorBgLayout: "#0b1220",
    colorBgContainer: "#111a2e",
    colorBorderSecondary: "#1e2a44",
    borderRadius: 8,
    fontSize: 13,
  },
  components: {
    Card: { colorBgContainer: "#111a2e" },
    Table: { colorBgContainer: "#111a2e", headerBg: "#16223c" },
    Tabs: { cardBg: "#16223c" },
  },
};

/** 图表配色（模型环形图/部门柱等复用）。 */
export const chartPalette = [
  "#3b82f6", "#22c55e", "#f59e0b", "#a855f7",
  "#06b6d4", "#ef4444", "#eab308", "#ec4899",
];
