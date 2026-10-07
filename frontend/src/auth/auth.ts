/** Cognito Hosted UI OAuth2 授权码 + PKCE 流程。
 *
 * - login()：跳转 Hosted UI /authorize。
 * - handleCallback()：用回调 code 换 token（/oauth2/token），存内存。
 * - logout()：清 token + 跳 Hosted UI /logout。
 *
 * token 存内存（避免 localStorage 的 XSS 风险，配合 CloudFront 安全头）；
 * 刷新页面会丢失，交由路由守卫在无 token 时重新走登录（Cognito 会话仍在则静默返回）。
 */
import { getConfig } from "../config";
import { challengeFromVerifier, randomState, randomVerifier } from "./pkce";

const PKCE_KEY = "kiro_pkce_verifier";
const STATE_KEY = "kiro_oauth_state";

interface Tokens {
  idToken: string;
  accessToken: string;
  expiresAt: number; // epoch ms
  email?: string;
}

let _tokens: Tokens | null = null;

export function isAuthenticated(): boolean {
  return !!_tokens && _tokens.expiresAt > Date.now() + 5000;
}

export function getIdToken(): string | null {
  return isAuthenticated() ? _tokens!.idToken : null;
}

/** access token 用于调用需 client_id 校验的下游（如 AgentCore 入站 JWT）。 */
export function getAccessToken(): string | null {
  return isAuthenticated() ? _tokens!.accessToken : null;
}

export function getUserEmail(): string | undefined {
  return _tokens?.email;
}

export async function login(): Promise<void> {
  const { cognito } = getConfig();
  const verifier = randomVerifier();
  const state = randomState();
  sessionStorage.setItem(PKCE_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);
  const challenge = await challengeFromVerifier(verifier);
  const params = new URLSearchParams({
    response_type: "code",
    client_id: cognito.clientId,
    redirect_uri: cognito.redirectUri,
    scope: cognito.scopes.join(" "),
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  window.location.assign(`${cognito.hostedUiDomain}/oauth2/authorize?${params}`);
}

export async function handleCallback(): Promise<void> {
  const { cognito } = getConfig();
  const url = new URL(window.location.href);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const savedState = sessionStorage.getItem(STATE_KEY);
  const verifier = sessionStorage.getItem(PKCE_KEY);

  if (!code) throw new Error("回调缺少 authorization code");
  if (!state || state !== savedState) throw new Error("state 校验失败（可能的 CSRF）");
  if (!verifier) throw new Error("缺少 PKCE verifier");

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: cognito.clientId,
    code,
    redirect_uri: cognito.redirectUri,
    code_verifier: verifier,
  });

  const resp = await fetch(`${cognito.hostedUiDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!resp.ok) throw new Error(`token 交换失败: ${resp.status} ${await resp.text()}`);
  const data = await resp.json();

  _tokens = {
    idToken: data.id_token,
    accessToken: data.access_token,
    expiresAt: Date.now() + (data.expires_in ?? 3600) * 1000,
    email: decodeEmail(data.id_token),
  };
  sessionStorage.removeItem(PKCE_KEY);
  sessionStorage.removeItem(STATE_KEY);
}

export function logout(): void {
  const { cognito } = getConfig();
  _tokens = null;
  const params = new URLSearchParams({
    client_id: cognito.clientId,
    logout_uri: cognito.logoutUri,
  });
  window.location.assign(`${cognito.hostedUiDomain}/logout?${params}`);
}

function decodeEmail(idToken: string): string | undefined {
  try {
    const payload = idToken.split(".")[1];
    const json = JSON.parse(
      atob(payload.replace(/-/g, "+").replace(/_/g, "/"))
    );
    return json.email as string | undefined;
  } catch {
    return undefined;
  }
}
