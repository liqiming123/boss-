import { ApiClient } from "@recruitment/api-client";
import { clearAuth, getAuth, setAuth, type AuthState } from "./auth-store";
async function refreshAccess(auth: AuthState) {
  if (!auth.refreshToken) return null;
  const response = await fetch(`${auth.apiBaseUrl}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      refresh_token: auth.refreshToken,
      device_id: auth.deviceId,
    }),
  });
  if (!response.ok) {
    await clearAuth();
    return null;
  }
  const data = (await response.json()) as { access_token: string };
  await setAuth({ accessToken: data.access_token });
  return data.access_token;
}

function authenticatedClient(auth: AuthState) {
  let token = auth.accessToken ?? null;
  return new ApiClient(
    auth.apiBaseUrl,
    () => token,
    async () => (token = await refreshAccess(auth)),
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
