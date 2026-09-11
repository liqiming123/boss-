import { dispatchDailyClick } from "./daily-real-click";
import { apiRequest } from "./api-client";
import { getAuth } from "./auth-store";
import { REPORT_URL, isReportUrl } from "../adapters/boss/company-daily";
import { delay } from "../shared/delay";

type Plan = { enabled: boolean; account_display_name: string; dates: string[] };
type Job = { tabId: number; started: number; account: string; dates: string[]; scheduled?: boolean; targetDate?: string; useCurrentDayDefault?: boolean };
let busy = false;
const JOB = "companyDailyJob";
export const DAILY_ALARM = "company-daily-midnight";
export const RETRY_ALARM = "company-daily-check";
export const DAILY_TEST_ALARM = "company-daily-e2e-test";

async function pageAccount(tabId: number) {
  let identity = await chrome.tabs.sendMessage(tabId, { type: "GET_PAGE_ACCOUNT" }).catch(() => null);
  if (!identity?.ok) {
    await chrome.tabs.reload(tabId).catch(() => undefined);
    for (let attempt = 0; attempt < 30 && !identity?.ok; attempt += 1) {
      await delay(500);
      identity = await chrome.tabs.sendMessage(tabId, { type: "GET_PAGE_ACCOUNT" }).catch(() => null);
    }
  }
  return identity;
}

export async function scheduleCompanyDaily() {
  const time = Date.now(), day = 86400000, offset = 8 * 3600000;
  const next = Math.floor((time + offset - 30 * 60000) / day) * day + day - offset + 30 * 60000;
  await chrome.alarms.create(DAILY_ALARM, { when: next, periodInMinutes: 1440 });
}

/** Run a missed 23:30 job once when Chrome starts the worker after the alarm time. */
export async function runMissedCompanyDaily() {
  const now = new Date(Date.now() + 8 * 3600000);
  const today = now.toISOString().slice(0, 10);
  const minutes = now.getUTCHours() * 60 + now.getUTCMinutes();
  const target = minutes >= 23 * 60 + 30
    ? today
    : new Date(now.getTime() - 86400000).toISOString().slice(0, 10);
  const state = await chrome.storage.local.get("companyDailyAutoRunDate");
  if (state.companyDailyAutoRunDate === target) return;
  void startCompanyDaily(false, true);
}

export async function startCompanyDaily(refresh = false, scheduled = false) {
  if (busy) return;
  busy = true;
  try {
    // Clear a previous failure immediately so the popup never presents an
    // obsolete error while the enterprise report is loading.
    await chrome.storage.local.set({ companyDailyStatus: { status: "检查中", updatedAt: Date.now() } });
    if (!(await getAuth()).accessToken) return;
    const state = await chrome.storage.local.get([JOB, "companyDailyAttempt"]);
    const old = state[JOB] as Job | undefined;
    if (old) {
      const tab = await chrome.tabs.get(old.tabId).catch(() => undefined);
      // A manual retry is an explicit request to replace a stale or failed
      // in-flight collector (including one left behind by an extension
      // reload). Unattended retries still keep the duplicate-tab guard.
      if (!refresh && tab && Date.now() - old.started < 10 * 60000) return;
      if (isReportUrl(tab?.url)) await chrome.tabs.remove(old.tabId);
      await chrome.storage.local.remove(JOB);
    }
    // A user-triggered refresh is also used for historical backfills. It must
    // be able to start immediately after a prior attempt; the cooldown still
    // protects unattended alarm/retry checks from opening duplicate tabs.
    if (!refresh && Date.now() - (state.companyDailyAttempt || 0) < 4 * 60000) return;
    await chrome.storage.local.set({ companyDailyAttempt: Date.now() });
    const query = scheduled ? "?scheduled=true" : refresh ? "?refresh=true" : "";
    const plan = await apiRequest<Plan>(`/plugin/company-daily-data/plan${query}`);
    await chrome.storage.local.set({ companyDailyStatus: { status: "准备打开公司报表", updatedAt: Date.now() } });
    if (!plan.enabled || !plan.dates?.length) {
      await chrome.storage.local.set({ companyDailyStatus: { status: plan.enabled ? "昨日数据已采集，等待下次每日同步" : "当前账号未开通公司日报采集", updatedAt: Date.now() } });
      return;
    }
    const tabs = await chrome.tabs.query({ url: ["https://www.zhipin.com/web/chat/*", "https://zhipin.com/web/chat/*"] });
    let matched = false;
    for (const tab of tabs) {
      if (tab.id == null) continue;
      // Reloaded unpacked extensions are not injected into tabs that were
      // already open. BOSS also needs several seconds to render the account
      // identity, so wait for an affirmative response instead of treating an
      // early {ok:false} response as a permanent mismatch.
      const identity = await pageAccount(tab.id);
      if (identity?.ok && identity.displayName === plan.account_display_name) { matched = true; break; }
    }
    if (!matched) {
      await chrome.storage.local.set({ companyDailyStatus: { status: `等待打开 ${plan.account_display_name} 的 BOSS 沟通页`, updatedAt: Date.now() } });
      await chrome.alarms.create(RETRY_ALARM, { delayInMinutes: 1 });
      return;
    }
    // BOSS's enterprise report controls are custom focus-dependent widgets.
    // A background tab can render the page but silently ignore the synthetic
    // click used to switch “近七天” to “按天查看”. Run the collector in a
    // visible, active tab so the page has a real interaction context.
    const tab = await chrome.tabs.create({ url: REPORT_URL, active: true });
    if (tab.id == null) return;
    const shanghaiNow = new Date(Date.now() + 8 * 3600000);
    const shanghaiToday = shanghaiNow.toISOString().slice(0, 10);
    const shanghaiMinutes = shanghaiNow.getUTCHours() * 60 + shanghaiNow.getUTCMinutes();
    const useCurrentDayDefault = scheduled && shanghaiMinutes >= 23 * 60 + 30 && plan.dates[0] === shanghaiToday;
    await chrome.storage.local.set({ [JOB]: { tabId: tab.id, started: Date.now(), account: plan.account_display_name, dates: plan.dates, scheduled, targetDate: scheduled ? plan.dates[0] : undefined, useCurrentDayDefault } satisfies Job,
      companyDailyStatus: { status: "采集中", updatedAt: Date.now() } });
  } catch {
    await chrome.storage.local.set({ companyDailyStatus: { status: "等待重试，请确认扩展已登录", updatedAt: Date.now() } });
    await chrome.alarms.create(RETRY_ALARM, { delayInMinutes: 1 });
  } finally { busy = false; }
}

export async function dailyMessage(type: string, payload: any, sender: chrome.runtime.MessageSender) {
  const { [JOB]: job } = await chrome.storage.local.get(JOB) as Record<string, Job>;
  if (!job || sender.tab?.id !== job.tabId || !isReportUrl(sender.url) || sender.frameId !== 0 || !(await getAuth()).accessToken) {
    if (type === "DAILY_JOB") return null;
    throw new Error("DAILY_JOB_NOT_AUTHORIZED");
  }
  if (type === "DAILY_JOB") return job;
  if (type === "DAILY_REAL_CLICK") {
    await dispatchDailyClick(job.tabId, payload);
    return { ok: true };
  }
  if (type === "DAILY_BATCH") {
    if (payload?.account_display_name !== job.account || payload?.metric_date !== job.dates[0]) throw new Error("DAILY_DATE_MISMATCH");
    const result = await apiRequest("/plugin/company-daily-data/batch", { method: "POST", body: JSON.stringify(payload) });
    job.dates.shift();
    await chrome.storage.local.set({ [JOB]: job, companyDailyStatus: { status: "已提交飞书同步", date: payload.metric_date, rows: payload.rows.length, updatedAt: Date.now() } });
    return result;
  }
  if (type === "DAILY_FINISH" || type === "DAILY_FAILED") {
    if (type === "DAILY_FAILED") await chrome.storage.local.set({ companyDailyStatus: { status: "页面识别未完成，稍后重试", errorCode: /^DAILY_[A-Z_]+$/.test(payload?.errorCode || "") ? payload.errorCode : "DAILY_READ_FAILED", updatedAt: Date.now() } });
    if (type === "DAILY_FINISH" && job.scheduled && job.targetDate) {
      await chrome.storage.local.set({ companyDailyAutoRunDate: job.targetDate });
      await chrome.alarms.clear(RETRY_ALARM);
    }
    await chrome.storage.local.remove(JOB);
    // Keep failed report tabs open so the actual BOSS state remains inspectable
    // and the user can retry after correcting the page/session.
    if (type === "DAILY_FINISH") setTimeout(() => { void chrome.tabs.remove(job.tabId).catch(() => undefined); }, 500);
    return { ok: true };
  }
  throw new Error("DAILY_MESSAGE_INVALID");
}
