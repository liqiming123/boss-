import { afterEach, describe, expect, it, vi } from "vitest";
import { logoutExtension } from "../src/background/api-client";

describe("extension logout cleanup", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("removes auth, retry queues and account-scoped state before stopping tabs", async () => {
    const store: Record<string, unknown> = {
      apiBaseUrl: "https://api.example.com/api/v1",
      catchupEnabled: true,
      accessToken: "access-token",
      refreshToken: "refresh-token",
      deviceId: "device-id",
      pendingFeishuLogin: { attemptId: "attempt" },
      pendingMessageSentEvents: [{ client_event_id: "event-1" }],
      pendingSnapshotUploads: [{ candidateSourceIds: ["source-1"], parts: ["data:image/png;base64,x"], snapshotHash: "hash" }],
      companyDailyJob: { tabId: 42, account: "谢女士" },
      companyDailyAttempt: 123,
      companyDailyStatus: { status: "采集中" },
      companyDailyAutoRunDate: "2026-09-10",
      "boss-catchup-unread:谢女士": { "谢女士\u0000岗位": true },
      unrelatedPreference: "keep",
    };
    const removed: string[] = [];
    const get = vi.fn(async (keys: string | string[] | null) => {
      if (keys === null) return { ...store };
      const list = Array.isArray(keys) ? keys : [keys];
      return Object.fromEntries(list.filter((key) => key in store).map((key) => [key, store[key]]));
    });
    const remove = vi.fn(async (keys: string | string[]) => {
      for (const key of Array.isArray(keys) ? keys : [keys]) {
        removed.push(key);
        delete store[key];
      }
    });
    const clear = vi.fn(async (name: string) => Boolean(name));
    const sendMessage = vi.fn(async () => ({ ok: true }));
    const removeTab = vi.fn(async () => undefined);
    vi.stubGlobal("chrome", {
      storage: { local: { get, remove } },
      alarms: { clear },
      tabs: {
        query: vi.fn(async () => [{ id: 7, url: "https://www.zhipin.com/web/chat/index" }]),
        sendMessage,
        remove: removeTab,
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true })));

    await logoutExtension();

    expect(removed).toEqual(expect.arrayContaining([
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
      "boss-catchup-unread:谢女士",
    ]));
    expect(store.apiBaseUrl).toBe("https://api.example.com/api/v1");
    expect(store.catchupEnabled).toBe(true);
    expect(store.unrelatedPreference).toBe("keep");
    expect(clear.mock.calls.map(([name]) => name)).toEqual(expect.arrayContaining([
      "recruitment-message-retry",
      "recruitment-snapshot-retry",
      "company-daily-check",
      "company-daily-e2e-test",
    ]));
    expect(removeTab).toHaveBeenCalledWith(42);
    expect(sendMessage).toHaveBeenCalledWith(7, { type: "EXTENSION_LOGGED_OUT" });
    expect(fetch).toHaveBeenCalledWith(
      "https://api.example.com/api/v1/plugin/device/logout",
      expect.objectContaining({
        method: "POST",
        headers: { Authorization: "Bearer access-token" },
      }),
    );
  });
});
