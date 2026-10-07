import { Select } from "antd";
import { GlobalOutlined } from "@ant-design/icons";
import { LANGUAGES, useI18n, type Lang } from "../i18n";

/** UI 语言切换下拉框（简体中文 / English）。 */
export function LanguageSwitch() {
  const { lang, setLang } = useI18n();
  return (
    <Select
      size="small"
      variant="borderless"
      value={lang}
      onChange={(v) => setLang(v as Lang)}
      style={{ width: 120 }}
      suffixIcon={<GlobalOutlined style={{ color: "#cbd5e1" }} />}
      options={LANGUAGES.map((l) => ({ value: l.value, label: l.label }))}
    />
  );
}
