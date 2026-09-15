import { ApiClient } from "@recruitment/api-client";
import { clearAuth, getAuth, setAuth, type AuthState } from "./auth-store";
async function refreshAccess(auth: AuthState, signal?: AbortSignal) {
  if (!auth.refreshToken) return null;
  const response = await fetch(`${auth.apiBaseUrl}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      refresh_token: auth.refreshToken,
      device_id: auth.deviceId,
    }),
  });
  if (!response.ok) {
    await clearAuth();
    await clearLogoutState();
    return null;
  }
  const data = (await response.json()) as { access_token: string };
  const current = await getAuth();
  if (current.refreshToken !== auth.refreshToken || current.deviceId !== auth.deviceId)
    return null;
  await setAuth({ accessToken: data.access_token });
  return data.access_token;
}

function authenticatedClient(auth: AuthState) {
  let token = auth.accessToken ?? null;
  return new ApiClient(
    auth.apiBaseUrl,
    () => token,
    async (signal) => (token = await refreshAccess(auth, signal)),
    () => void clearAuth(),
  );
}

async function requestWithTimeout<T>(
  auth: AuthState,
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await authenticatedClient(auth).request<T>(path, {
      ...init,
      signal: init.signal ?? controller.signal,
    });
  } catch (error) {
    // Chrome reports AbortController timeouts with the implementation-specific
    // message "signal is aborted without reason". Do not leak that message to
    // users; it is neither actionable nor stable across browser versions.
    if (!init.signal && controller.signal.aborted) {
      throw new Error("请求超时，请检查连接设置或稍后重试", { cause: error });
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export async function apiRequest<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  return requestWithTimeout(await getAuth(), path, init, 8_000);
}
export async function apiFormRequest<T = unknown>(
  path: string,
  form: FormData,
): Promise<T> {
  return requestWithTimeout(
    await getAuth(),
    path,
    { method: "POST", body: form },
    60_000,
  );
}

const LOGOUT_STORAGE_KEYS = [
  "accessToken",
  "refreshToken",
  "deviceId",
  "pendingFeishuLogin",
  "pendingMessageSentEvents",
  "pendingSnapshotUploads",
  "companyDailyJob",
  "companyDailyAttempt",
  "companyDailyStatus",
  "companyDailyAutoRunDate",
];
const LOGOUT_ALARM_NAMES = [
  "recruitment-message-retry",
  "recruitment-snapshot-retry",
  "company-daily-check",
  "company-daily-e2e-test",
];

async function clearLogoutState(): Promise<void> {
  // Read the job before removing it so an in-flight report tab can be closed.
  // The periodic midnight alarm is intentionally preserved: it is a global
  // extension schedule and simply no-ops while the device is logged out.
  const stored = (await chrome.storage.local.get(null)) as Record<string, unknown>;
  const dailyJob = stored.companyDailyJob;
  const reportTabId =
    dailyJob && typeof dailyJob === "object" && "tabId" in dailyJob &&
    typeof (dailyJob as { tabId?: unknown }).tabId === "number"
      ? (dailyJob as { tabId: number }).tabId
      : undefined;
  const unreadKeys = Object.keys(stored).filter((key) =>
    key.startsWith("boss-catchup-unread:"),
  );
  await chrome.storage.local.remove([
    ...new Set([...LOGOUT_STORAGE_KEYS, ...unreadKeys]),
  ]);
  await Promise.all(LOGOUT_ALARM_NAMES.map((name) => chrome.alarms.clear(name)));
  if (reportTabId !== undefined)
    await chrome.tabs.remove(reportTabId).catch(() => undefined);
}

export async function logoutExtension(): Promise<void> {
  const auth = await getAuth();
  // Remove local authority and all account-scoped transient state first so
  // open pages stop even when server-side revocation is slow.
  await clearLogoutState();
  if (auth.accessToken && auth.deviceId) {
    try {
      await fetch(`${auth.apiBaseUrl}/plugin/device/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${auth.accessToken}` },
        signal: AbortSignal.timeout(8_000),
      });
    } catch {
      // Logout remains local even if the API is unavailable; the device record
      // can be cleaned up by the administrator later.
    }
  }
  // A content script keeps its own five-minute timer and may be mid-scan.
  // Explicitly tell every BOSS tab to stop immediately; clearing storage alone
  // cannot cancel timers in an already-running page.
  const tabs = await chrome.tabs.query({
    url: ["https://www.zhipin.com/web/chat/*", "https://zhipin.com/web/chat/*", "https://bosszhipin.com/web/chat/*", "https://*.bosszhipin.com/web/chat/*"],
  });
  await Promise.all(tabs.map(async (tab) => {
    if (tab.id == null) return;
    try { await chrome.tabs.sendMessage(tab.id, { type: "EXTENSION_LOGGED_OUT" }); } catch { /* tab may not have the content script yet */ }
  }));
}
