import { getAuth } from "./auth-store";

/**
 * One long-lived SSE connection per device for time-sensitive duplicate alerts.
 *
 * The stream is a delivery accelerator, never a source of truth: every alert it
 * carries also exists as a PostgreSQL alert row and a Feishu direct message, and
 * a dropped frame is recovered by the next page check. The connection therefore
 * reconnects quietly and never surfaces transport failures to the recruiter.
 */

export type LiveAlertEvent = {
  event?: string;
  lookup_alert_id?: string;
  candidate_name?: string;
  job_name?: string;
  viewer_recruiter_id?: string;
  viewer_name?: string;
  matched_recruiter_id?: string | null;
  matched_recruiter_name?: string;
  match_level?: string;
  match_reason?: string;
  first_contact_at?: string | null;
  last_activity_at?: string | null;
  notification_version?: number;
  own_history?: boolean;
};

const BOSS_TAB_URLS = [
  "https://www.zhipin.com/web/chat/*",
  "https://zhipin.com/web/chat/*",
  "https://bosszhipin.com/web/chat/*",
  "https://*.bosszhipin.com/web/chat/*",
];

const INITIAL_BACKOFF_MS = 2_000;
const MAX_BACKOFF_MS = 60_000;

let controller: AbortController | null = null;
let reconnectTimer: number | undefined;
let backoffMs = INITIAL_BACKOFF_MS;

function stopConnection(): void {
  controller?.abort();
  controller = null;
  if (reconnectTimer !== undefined) {
    clearTimeout(reconnectTimer);
    reconnectTimer = undefined;
  }
}

/** (Re)connect the live channel; safe to call repeatedly. */
export function startAlertStream(): void {
  stopConnection();
  void connect();
}

export function stopAlertStream(): void {
  backoffMs = INITIAL_BACKOFF_MS;
  stopConnection();
}

function scheduleReconnect(): void {
  if (reconnectTimer !== undefined) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = undefined;
    void connect();
  }, backoffMs) as unknown as number;
  backoffMs = Math.min(MAX_BACKOFF_MS, backoffMs * 2);
}

async function connect(): Promise<void> {
  const auth = await getAuth();
  if (!auth.accessToken || !auth.apiBaseUrl) {
    // Not logged in yet. Reconcile-login calls startAlertStream when it is.
    return;
  }
  const abort = new AbortController();
  controller = abort;
  try {
    const response = await fetch(`${auth.apiBaseUrl}/plugin/stream`, {
      headers: { Authorization: `Bearer ${auth.accessToken}`, Accept: "text/event-stream" },
      signal: abort.signal,
    });
    if (!response.ok || !response.body) {
      scheduleReconnect();
      return;
    }
    backoffMs = INITIAL_BACKOFF_MS;
    await consume(response.body);
  } catch {
    // Offline, tab suspended, server restart or a rejected token: retry with
    // backoff. A revoked device simply keeps failing silently, which is the
    // correct behaviour for a background accelerator.
  }
  if (controller === abort) {
    controller = null;
    scheduleReconnect();
  }
}

async function consume(body: ReadableStream<Uint8Array>): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      // SSE frames are separated by a blank line; a partial frame stays buffered.
      let separator = buffer.indexOf("\n\n");
      while (separator !== -1) {
        const frame = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);
        handleFrame(frame);
        separator = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function handleFrame(frame: string): void {
  let eventName = "message";
  const dataLines: string[] = [];
  for (const rawLine of frame.split("\n")) {
    const line = rawLine.trimEnd();
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (eventName !== "duplicate_lookup" || !dataLines.length) return;
  try {
    const payload = JSON.parse(dataLines.join("\n")) as LiveAlertEvent;
    if (payload.event !== "duplicate_lookup") return;
    void broadcast(payload);
  } catch {
    // A malformed frame is dropped; the authoritative record is the alert row.
  }
}

async function broadcast(payload: LiveAlertEvent): Promise<void> {
  const tabs = await chrome.tabs.query({ url: BOSS_TAB_URLS }).catch(() => []);
  await Promise.all(
    tabs.map(async (tab) => {
      if (tab.id == null) return;
      try {
        await chrome.tabs.sendMessage(tab.id, { type: "LIVE_DUPLICATE_ALERT", payload });
      } catch {
        // The tab may be mid-navigation or not host the content script yet.
      }
    }),
  );
}
