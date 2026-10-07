import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider, App as AntdApp, Spin } from "antd";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";
import { BrowserRouter } from "react-router-dom";

import { darkTheme } from "./theme";
import { loadConfig } from "./config";
import { AppRoutes } from "./App";
import { LanguageProvider, useI18n } from "./i18n";
import "./index.css";

/** 读取当前语言，驱动 AntD 组件 locale。 */
function LocalizedApp() {
  const { lang } = useI18n();
  return (
    <ConfigProvider theme={darkTheme} locale={lang === "en" ? enUS : zhCN}>
      <AntdApp>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </AntdApp>
    </ConfigProvider>
  );
}

function Root() {
  const [ready, setReady] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);

  React.useEffect(() => {
    loadConfig()
      .then(() => setReady(true))
      .catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div style={{ padding: 40, color: "#f87171" }}>Config load failed: {err}</div>;
  if (!ready)
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh" }}>
        <Spin size="large" tip="Loading…" />
      </div>
    );

  return (
    <LanguageProvider>
      <LocalizedApp />
    </LanguageProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
