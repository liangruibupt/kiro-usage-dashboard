/** 运行时配置：从 public/config.json 加载，避免把环境写死进构建产物。
 *  WebStack 部署时会用真实的 API/Cognito 值覆盖 config.json。 */

export interface CognitoConfig {
  region: string;
  userPoolId: string;
  clientId: string;
  hostedUiDomain: string;
  redirectUri: string;
  logoutUri: string;
  scopes: string[];
}

export interface AppConfig {
  apiEndpoint: string;
  cognito: CognitoConfig;
  creditUnitPrice: number;
}

let _cfg: AppConfig | null = null;

export async function loadConfig(): Promise<AppConfig> {
  if (_cfg) return _cfg;
  const resp = await fetch("/config.json", { cache: "no-store" });
  if (!resp.ok) throw new Error(`无法加载运行时配置 config.json: ${resp.status}`);
  _cfg = (await resp.json()) as AppConfig;
  return _cfg;
}

export function getConfig(): AppConfig {
  if (!_cfg) throw new Error("配置尚未加载，请先调用 loadConfig()");
  return _cfg;
}
