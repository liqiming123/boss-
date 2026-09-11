import { apiRequest } from "./api-client";
import { getAuth, setAuth } from "./auth-store";
import {
  flushMessageQueue,
  queueMessageSent,
  shouldRetryMessageSent,
} from "./message-sent-queue";
import {
  flushSnapshotQueue,
  queueSnapshot,
  shouldRetrySnapshot,
  uploadSnapshotPayload,
} from "./snapshot-upload-queue";
import { uploadResumeFromUrl, uploadResumeScreenshot } from "./resume-upload";
import { dailyMessage, scheduleCompanyDaily, runMissedCompanyDaily, startCompanyDaily, DAILY_ALARM, RETRY_ALARM, DAILY_TEST_ALARM } from "./company-daily";

const allowed = new Set([
  "GET_AUTH",
  "GET_BOUND_ACCOUNT",
  "SET_AUTH",
  "CHECK_CONTEXT",
  "MESSAGE_SENT",
  "SYNC_CONVERSATION",
  "GET_SCAN_CHECKPOINT",
  "GET_CONVERSATION_INDEX",
  "GET_PLUGIN_SETTINGS",
  "PUT_SCAN_CHECKPOINT",
  "CAPTURE_VISIBLE_TAB",
  "UPLOAD_SNAPSHOT",
  "REPORT_SNAPSHOT_STATUS",
  "UPLOAD_RESUME_FROM_URL",
  "UPLOAD_RESUME_SCREENSHOT",
  "RECORD_EVENT",
  "SEND_DIAGNOSTIC",
  "DAILY_CHECK", "DAILY_TIMER_TEST", "DAILY_REAL_CLICK", "DAILY_JOB", "DAILY_BATCH", "DAILY_FINISH", "DAILY_FAILED",
]);
const routes: Record<string, string> = {
  CHECK_CONTEXT: "/plugin/context/check",
  MESSAGE_SENT: "/plugin/engagements/message-sent",
  SYNC_CONVERSATION: "/plugin/conversations/sync",
  PUT_SCAN_CHECKPOINT: "/plugin/conversations/checkpoint",
  RECORD_EVENT: "/plugin/events",
  SEND_DIAGNOSTIC: "/plugin/diagnostics",
  REPORT_SNAPSHOT_STATUS: "/plugin/conversations/snapshot-status",
};

async function handle(message: unknown, sender?: chrome.runtime.MessageSender): Promise<unknown> {
  if (
    !message ||
    typeof message !== "object" ||
    !("type" in message) ||
    !allowed.has(String(message.type))
  )
    throw new Error("MESSAGE_NOT_ALLOWED");
  const m = message as { type: string; payload?: unknown };
  if (m.type === "DAILY_CHECK") { void startCompanyDaily((m.payload as any)?.refresh === true); return { ok: true }; }
  if (m.type === "DAILY_TIMER_TEST") {
    await chrome.alarms.create(DAILY_TEST_ALARM, { delayInMinutes: 1 });
    await chrome.storage.local.set({ companyDailyStatus: { status: "定时测试已安排，1 分钟后自动同步", updatedAt: Date.now() } });
    return { ok: true };
  }
  if (m.type.startsWith("DAILY_")) return dailyMessage(m.type, m.payload, sender || {});
  if (m.type === "GET_AUTH") return getAuth();
  if (m.type === "GET_BOUND_ACCOUNT") return apiRequest<{ display_name: string }>("/plugin/me");
  if (m.type === "SET_AUTH") {
    await setAuth(m.payload as Record<string, string>);
    void flushMessageQueue();
    return { ok: true };
  }
  if (!(await getAuth()).accessToken) throw new Error("EXTENSION_LOGGED_OUT");
  if (m.type === "GET_SCAN_CHECKPOINT") {
    const p = m.payload as { account_display_name: string; platform?: string };
    return apiRequest(
      `/plugin/conversations/checkpoint?account_display_name=${encodeURIComponent(p.account_display_name)}&platform=${encodeURIComponent(p.platform ?? "boss")}`,
    );
  }
  if (m.type === "GET_CONVERSATION_INDEX") {
    const p = m.payload as { account_display_name: string; platform?: string };
    return apiRequest(
      `/plugin/conversations/index?account_display_name=${encodeURIComponent(p.account_display_name)}&platform=${encodeURIComponent(p.platform ?? "boss")}`,
    );
  }
  if (m.type === "GET_PLUGIN_SETTINGS") {
    const settings = await apiRequest<{ catchup_enabled: boolean }>("/plugin/settings");
    await setAuth({ catchupEnabled: settings.catchup_enabled });
    return settings;
  }
  if (m.type === "CAPTURE_VISIBLE_TAB") {
    // Use CDP so historical screenshots work while the user views Feishu,
    // without activating the BOSS tab and causing visible flicker.
    const targetTabId = sender?.tab?.id;
    if (targetTabId == null)
      throw new Error("SCREENSHOT_SOURCE_TAB_UNAVAILABLE");
    const target = { tabId: targetTabId };
    await chrome.debugger.attach(target, "1.3");
    try {
      await chrome.debugger.sendCommand(target, "Page.enable");
      // Chrome now rejects Page.captureScreenshot requests with
      // `fromSurface: false` ("Only screenshots from surface are allowed.").
      // Always use the compositor surface path.  The request remains scoped
      // to the BOSS tab supplied by the content-script and never captures the
      // surrounding browser UI.
      const result = await chrome.debugger.sendCommand(target, "Page.captureScreenshot", {
        format: "png",
        fromSurface: true,
        captureBeyondViewport: false,
      }) as { data?: string };
      if (!result.data) throw new Error("SCREENSHOT_CAPTURE_FAILED");
      return {
        dataUrl: `data:image/png;base64,${result.data}`,
      };
    } finally {
      await chrome.debugger.detach(target).catch(() => undefined);
    }
  }
  if (m.type === "UPLOAD_SNAPSHOT") {
    const p = m.payload as {
      candidateSourceIds: string[];
      parts: string[];
      snapshotHash: string;
    };
    try {
      return await uploadSnapshotPayload(p);
    } catch (error) {
      if (shouldRetrySnapshot(error)) await queueSnapshot(p);
      throw error;
    }
  }
  if (m.type === "UPLOAD_RESUME_FROM_URL")
    return uploadResumeFromUrl(
      m.payload as Parameters<typeof uploadResumeFromUrl>[0],
    );
  if (m.type === "UPLOAD_RESUME_SCREENSHOT")
    return uploadResumeScreenshot(
      m.payload as Parameters<typeof uploadResumeScreenshot>[0],
    );
  const route = routes[m.type];
  if (!route) throw new Error("MESSAGE_NOT_ALLOWED");
  try {
    return await apiRequest(route, {
      method:
        m.type === "PUT_SCAN_CHECKPOINT" || m.type === "REPORT_SNAPSHOT_STATUS"
          ? "PUT"
          : "POST",
      body: JSON.stringify(m.payload),
    });
  } catch (error) {
    if (m.type === "MESSAGE_SENT" && shouldRetryMessageSent(error)) {
      await queueMessageSent(m.payload);
      chrome.alarms.create("recruitment-message-retry", { periodInMinutes: 1 });
      throw new Error("MESSAGE_SENT_QUEUED_FOR_RETRY");
    }
    throw error;
  }
}

chrome.runtime.onMessage.addListener(
  (message: unknown, _sender, sendResponse) => {
    void handle(message, _sender)
      .then((data) => sendResponse({ ok: true, data }))
      .catch((error) =>
        sendResponse({
          ok: false,
          error: error instanceof Error ? error.message : "UNKNOWN_ERROR",
        }),
      );
    return true;
  },
);
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === DAILY_ALARM) {
    void startCompanyDaily(false, true);
  }
  if (alarm.name === RETRY_ALARM) void runMissedCompanyDaily();
  if (alarm.name === DAILY_TEST_ALARM) void startCompanyDaily(false, true);
  if (alarm.name === "recruitment-message-retry") void flushMessageQueue();
  if (alarm.name === "recruitment-snapshot-retry") void flushSnapshotQueue();
});
void flushMessageQueue();
void flushSnapshotQueue();
void scheduleCompanyDaily();
void runMissedCompanyDaily();
