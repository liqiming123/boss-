export type PendingFeishuLogin = {
  attemptId: string;
  pollToken: string;
  expiresAt: number;
};
export type AuthState = {
  apiBaseUrl: string;
  accessToken?: string;
  refreshToken?: string;
  deviceId?: string;
  pendingFeishuLogin?: PendingFeishuLogin;
  catchupEnabled: boolean;
};
const PRODUCTION_API_BASE_URL = "https://ai.wuxistar.com/api/v1";
const LOCAL_API_PATTERN = /^http:\/\/(?:localhost|127\.0\.0\.1):8000\/api\/v1\/?$/;
const defaults: AuthState = {
  apiBaseUrl: PRODUCTION_API_BASE_URL,
  catchupEnabled: true,
};
const keys: Array<keyof AuthState> = [
  "apiBaseUrl",
  "accessToken",
  "refreshToken",
  "deviceId",
  "pendingFeishuLogin",
  "catchupEnabled",
];
export async function getAuth(): Promise<AuthState> {
  const stored = await chrome.storage.local.get(keys);
  // Migrate installations that still carry the old development endpoint.
  // This prevents a previously saved localhost value from overriding the
  // production default after an extension update.
  if (typeof stored.apiBaseUrl === "string" && LOCAL_API_PATTERN.test(stored.apiBaseUrl)) {
    stored.apiBaseUrl = PRODUCTION_API_BASE_URL;
    delete stored.accessToken;
    delete stored.refreshToken;
    delete stored.pendingFeishuLogin;
    await chrome.storage.local.remove([
      "accessToken",
      "refreshToken",
      "pendingFeishuLogin",
    ]);
    await chrome.storage.local.set({ apiBaseUrl: PRODUCTION_API_BASE_URL });
  }
  return {
    ...defaults,
    ...stored,
  } as AuthState;
}
export async function setAuth(value: Partial<AuthState>) {
  await chrome.storage.local.set(value);
}
export async function clearAuth() {
  await chrome.storage.local.remove([
    "accessToken",
    "refreshToken",
    "pendingFeishuLogin",
  ]);
}
