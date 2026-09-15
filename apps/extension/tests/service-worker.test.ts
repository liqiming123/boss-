import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The background worker maps message types to endpoints. A wrong HTTP verb
 * silently 405s at runtime (the scan report did exactly that), so pin the
 * verbs here.
 */
const AUTH = {
  apiBaseUrl: "https://api.example.com/api/v1",
  accessToken: "test-token",
  deviceId: "device-1",
  catchupEnabled: true,
};

type Listener = (
  message: unknown,
  sender: unknown,
  sendResponse: (value: unknown) => void,
) => boolean;

async function loadWorker() {
  const listeners: Listener[] = [];
  const stored: Record<string, unknown> = { ...AUTH };
  const storage = {
    get: vi.fn(async (keys?: string | string[] | null) => {
      if (!keys) return { ...stored };
      const requested = typeof keys === "string" ? [keys] : keys;
      return Object.fromEntries(requested.map((key) => [key, stored[key]]));
    }),
    set: vi.fn(async (values: Record<string, unknown>) => {
      Object.assign(stored, values);
    }),
    remove: vi.fn(async (keys: string | string[]) => {
      for (const key of typeof keys === "string" ? [keys] : keys) delete stored[key];
    }),
  };
  vi.stubGlobal("chrome", {
    runtime: {
      onMessage: {
        addListener: (listener: Listener) => listeners.push(listener),
      },
      onInstalled: { addListener: vi.fn() },
      getManifest: () => ({ version: "test" }),
      sendMessage: vi.fn(),
    },
    storage: { local: storage, onChanged: { addListener: vi.fn() } },
    alarms: {
      create: vi.fn(async () => undefined),
      clear: vi.fn(async () => true),
      onAlarm: { addListener: vi.fn() },
    },
    tabs: { query: vi.fn(async () => []), sendMessage: vi.fn(), remove: vi.fn() },
    action: { setBadgeText: vi.fn() },
    debugger: { attach: vi.fn(), detach: vi.fn(), sendCommand: vi.fn() },
  });
  vi.resetModules();
  await import("../src/background/service-worker");
  return listeners[0];
}

function send(listener: Listener, message: unknown) {
  return new Promise((resolve) => listener(message, {}, resolve));
}

describe("background worker endpoint methods", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("reports a finished scan with POST, the verb the API exposes", async () => {
    const calls: Array<{ url: string; method?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method });
      return new Response(JSON.stringify({ accepted: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }));
    const listener = await loadWorker();
    const response = (await send(listener, {
      type: "REPORT_SCAN_COMPLETED",
      payload: { account_display_name: "李先生", completed_through_at: "2026-09-14T08:00:00Z" },
    })) as { ok: boolean };
    expect(response.ok).toBe(true);
    // Worker boot may flush other queues; assert on this endpoint only.
    const report = calls.filter((call) => call.url.includes("/plugin/conversations/scan-report"));
    expect(report).toHaveLength(1);
    // PUT to this path returned 405 in production.
    expect(report[0].method).toBe("POST");
  });

  it("still reports snapshot status with PUT", async () => {
    const calls: Array<{ url: string; method?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method });
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }));
    const listener = await loadWorker();
    await send(listener, {
      type: "REPORT_SNAPSHOT_STATUS",
      payload: { candidate_source_ids: ["source"], status: "FAILED", error_code: "X" },
    });
    const status = calls.filter((call) => call.url.includes("/plugin/conversations/snapshot-status"));
    expect(status).toHaveLength(1);
    expect(status[0].method).toBe("PUT");
  });
});
