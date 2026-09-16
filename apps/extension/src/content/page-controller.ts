import type {
  RecruiterMessageSent,
  RecruitmentSiteAdapter,
} from "../adapters/types";
import type { ContextResponse } from "@recruitment/api-client";
import { PanelController } from "./panel-controller";
import {
  captureBossConversationSnapshot,
  captureBossPreviewScreenshot,
  SnapshotCaptureError,
} from "../adapters/boss/boss-snapshot";
import {
  bossRowIdentity,
  currentBossListFilter,
  decideBossRow,
  isBossListActivityNewer,
  isWithinSweepScope,
  hasBossUnreadBadge,
  observeBossUnreadConversationClick,
  openBossCommunicatingFilter,
  parseBossRowIdentity,
  readBossMountedRows,
  restoreBossListFilter,
  runBossCatchup,
} from "../adapters/boss/boss-catchup";
import { sha256Hex } from "../shared/sha256";
import { sendRuntimeMessage } from "../shared/runtime-message";
import { delay } from "../shared/delay";
import { observeUserActivity } from "../shared/user-activity";

type ResolvedFields = {
  accountDisplayName: string | null;
  candidateDisplayName: string;
  candidateAge: number | null;
  candidateExperience: string | null;
  candidateEducation: string | null;
  jobDisplayName: string;
  platformCandidateId: string | null;
  conversationStartedAt: string | null;
  conversationUpdatedAt: string | null;
  hasRecruiterOutbound: boolean;
  recruitmentStatus: string;
  statusEvidence: string | null;
  statusRuleVersion: string;
  historicalJobs: string[];
  nativeCommunications: Array<{
    recruiterName: string;
    jobName: string;
    contactedAt: string;
    source: "BOSS_NATIVE";
  }>;
  resumeStatus: string;
  resumeDownload: { url: string; fileName: string } | null;
  fingerprint: string;
};

type StoredUnreadState = Record<string, boolean>;

// Polling cadence. Every pass walks the whole “沟通中” list, so an idle tab
// must not pay that cost every few minutes. One sweep runs per day, anchored at
// 23:30 Asia/Shanghai: the working day runs from 23:30 to the next 23:30, so a
// recruiter who is still talking to candidates after midnight is covered by the
// window they are in rather than one that already closed.
const CATCHUP_HOUR = 23;
const CATCHUP_MINUTE = 30;
const CATCHUP_RETRY_DELAY_MS = 60 * 1000;
const CATCHUP_MAX_RETRIES = 3;
const CATCHUP_WINDOW_MINUTES = CATCHUP_HOUR * 60 + CATCHUP_MINUTE;
// A sweep that was missed while the browser was closed must not be retried on
// every page reload: one attempt per hour is enough while the previous one is
// still unaccounted for.
const CATCHUP_HISTORY_MIN_GAP_MS = 60 * 60 * 1000;
// A missed-window sweep waits for a quiet page instead of starting the moment
// BOSS opens: the recruiter may have opened it to read or answer something, and
// a traversal switches the list filter and clicks through conversations.
const AUTO_SWEEP_QUIET_MS = 60 * 1000;
const AUTO_SWEEP_RECHECK_MS = 30 * 1000;
// Read-only duplicate-check watcher over the conversation list.
//
// A reply typed on a phone or in another browser updates the activity label of
// its own list row, but this tab receives no event for a conversation it does
// not have open — the page observer only re-checks the candidate on screen.
// This watcher closes exactly that gap, and nothing else: it re-reads the
// mounted rows, and for a row whose activity moved it runs the duplicate check
// only. It never clicks a row, never switches the filter and never calls the
// conversation sync, so it creates no candidate source, no Feishu row, no
// screenshot and no `MESSAGE_SENT` event.
const LIST_WATCH_INTERVAL_MS = 15 * 1000;
/** One round is a read, not a traversal: cap the requests a burst may fire. */
const LIST_WATCH_MAX_CHECKS_PER_ROUND = 5;
/** The server-side reason label for a check a list row triggered. */
const LIST_WATCH_SYNC_REASON = "LIST_ACTIVITY_DETECTED";

function shanghaiClock(now: number) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date(now));
  const get = (type: string) => Number(parts.find((part) => part.type === type)?.value);
  // Some ICU builds render midnight as hour 24 with `hour12: false`.
  return { year: get("year"), month: get("month"), day: get("day"), hour: get("hour") % 24, minute: get("minute") };
}

export function nextCatchupWindowDelayMs(now = Date.now()): number {
  const { year, month, day, hour, minute } = shanghaiClock(now);
  const passed = hour * 60 + minute >= CATCHUP_WINDOW_MINUTES;
  return Math.max(1_000, Date.UTC(year, month - 1, day + (passed ? 1 : 0), CATCHUP_HOUR - 8, CATCHUP_MINUTE) - now);
}

/**
 * A completed pass that never saw a single conversation row is not a success.
 *
 * BOSS paints the conversation list asynchronously and can leave skeleton rows
 * behind; the traversal then finishes "complete" without pricing anything. That
 * used to be recorded as a finished sweep, so an account whose list never
 * rendered simply reported success twice a day and produced no screenshots.
 */
export function isSuspiciousEmptyPass(
  result: { available: boolean; complete: boolean },
  traversed: number,
): boolean {
  return result.available && result.complete && traversed === 0;
}

/**
 * The sweep's progress, kept per BOSS account in `chrome.storage.local`.
 *
 * Everything here is a calendar day, not a timestamp: BOSS renders day-level
 * labels (`09月05日`) and a recruiter's "today" is the day they are working in,
 * so an anchor that compared precise times could sweep yesterday's evening
 * along with today or drop a row whose label carries no clock.
 */
export type CatchupHistoryState = {
  /** When the last sweep attempt started, completed or not. */
  last_attempt_at?: string;
  /** When a sweep actually finished a full pass. */
  last_completed_at?: string;
  /** `YYYY-MM-DD` the last finished sweep covered. This is the anchor. */
  swept_through_date?: string;
  /** Legacy timestamp anchor, still read so an upgrade keeps its place. */
  swept_through_at?: string;
};

/** The most recent 23:30 Asia/Shanghai sweep window that has already started. */
export function lastCatchupWindowMs(now = Date.now()): number {
  const { year, month, day, hour, minute } = shanghaiClock(now);
  const passed = hour * 60 + minute >= CATCHUP_WINDOW_MINUTES;
  // Before tonight's window the newest one is yesterday's. `Date.UTC`
  // normalises day 0 into the previous month/year.
  return Date.UTC(year, month - 1, day - (passed ? 0 : 1), CATCHUP_HOUR - 8, CATCHUP_MINUTE);
}

/** A calendar day as a sortable number: 2026-09-16 becomes 20260916. */
export function dateKey(value: Date | number): number {
  const date = value instanceof Date ? value : new Date(value);
  return date.getFullYear() * 10_000 + (date.getMonth() + 1) * 100 + date.getDate();
}

/** The `YYYY-MM-DD` form stored in the sweep state. */
export function dateKeyString(key: number): string {
  const year = Math.floor(key / 10_000);
  const month = Math.floor(key / 100) % 100;
  const day = key % 100;
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/**
 * The day a finished sweep covered, or null when the state carries none.
 *
 * A stored day wins; installs that still carry only the older timestamp anchor
 * (or a completion time) fall back to the day that value fell on.
 */
export function coveredDateKey(state: CatchupHistoryState): number | null {
  const stored = /^(\d{4})-(\d{2})-(\d{2})$/.exec((state.swept_through_date || "").trim());
  if (stored) return Number(stored[1]) * 10_000 + Number(stored[2]) * 100 + Number(stored[3]);
  const legacy = Date.parse(state.swept_through_at || "");
  if (Number.isFinite(legacy)) return dateKey(legacy);
  const completedAt = Date.parse(state.last_completed_at || "");
  return Number.isFinite(completedAt) ? dateKey(completedAt) : null;
}

/**
 * Where a sweep starts reading the list: the last day a finished sweep covered.
 *
 * Today is the scope. A sweep opens the conversations whose list activity falls
 * on a day the anchor does not already cover, so the normal case — yesterday's
 * sweep finished — covers today alone, and a browser that was closed over a
 * sweep day makes the next visit cover the missed days as well. A missing,
 * unusable or future anchor starts at yesterday, so a fresh install covers today
 * and never the whole conversation list.
 */
export function sweepAnchorDay(state: CatchupHistoryState, now = Date.now()): number {
  const today = dateKey(now);
  const covered = coveredDateKey(state);
  return covered === null || covered >= today ? dateKey(now - 86_400_000) : covered;
}

/**
 * The anchor a finished sweep records: the last day that has ended.
 *
 * A sweep only ever sees the day it runs in part-way through, so that day is not
 * covered yet, and recording it is what made the next morning's pass skip the
 * day that just ended. Recording yesterday instead means every pass covers the
 * day that just ended plus the one in progress — a browser that was closed at
 * 23:30 still gets yesterday, which is when the sweep actually runs in practice.
 */
export function nextSweepAnchor(now = Date.now()): string {
  return dateKeyString(dateKey(now - 86_400_000));
}

/**
 * True when a sweep window passed without a completed pass behind it.
 *
 * Leaving the BOSS tab open is supposed to be enough: the sweep runs at 23:30,
 * so a browser that was closed then used to wait for the next day with nothing
 * captured. The miss is what the next page visit makes up, once the page is
 * idle. This asks about windows, not days: the anchor deliberately trails a day
 * behind, and treating that as "still owed" would re-sweep on every reload.
 */
export function historyCatchupDueAt(state: CatchupHistoryState, now = Date.now()): boolean {
  const attemptedAt = Date.parse(state.last_attempt_at || "");
  if (Number.isFinite(attemptedAt) && now - attemptedAt < CATCHUP_HISTORY_MIN_GAP_MS) return false;
  const completedAt = Date.parse(state.last_completed_at || "");
  return !Number.isFinite(completedAt) || completedAt < lastCatchupWindowMs(now);
}

/**
 * True when opening this page should run the history sweep instead of the cheap
 * reconcile. Without a durable watermark store (no real extension context) the
 * cheap pass is kept rather than sweeping blindly.
 */
export function pageOpenSweepDue(
  state: CatchupHistoryState,
  hasWatermarkStore: boolean,
  now = Date.now(),
): boolean {
  return hasWatermarkStore && historyCatchupDueAt(state, now);
}

const catchupHistoryKey = (accountDisplayName: string) =>
  `boss-catchup-history:${accountDisplayName}`;

async function readCatchupHistory(key: string): Promise<CatchupHistoryState> {
  if (typeof chrome === "undefined" || !chrome.storage?.local) return {};
  return new Promise((resolve) => {
    chrome.storage.local.get(key, (value) => {
      const state = value?.[key];
      resolve(state && typeof state === "object" ? state as CatchupHistoryState : {});
    });
  });
}

function writeCatchupHistory(key: string, patch: CatchupHistoryState) {
  if (typeof chrome === "undefined" || !chrome.storage?.local) return;
  chrome.storage.local.get(key, (value) => {
    const stored = value?.[key];
    const state = stored && typeof stored === "object" ? stored as CatchupHistoryState : {};
    void chrome.storage.local.set({ [key]: { ...state, ...patch } });
  });
}

async function readUnreadState(key: string): Promise<StoredUnreadState> {
  if (typeof chrome === "undefined" || !chrome.storage?.local) return {};
  return new Promise((resolve) => {
    chrome.storage.local.get(key, (value) => {
      const state = value?.[key];
      resolve(state && typeof state === "object" ? state as StoredUnreadState : {});
    });
  });
}

function writeUnreadState(key: string, state: Map<string, boolean>) {
  if (typeof chrome === "undefined" || !chrome.storage?.local) return;
  // Keep only the latest bounded set; this is a detection watermark, not a
  // candidate data store.
  const entries = [...state.entries()].slice(-500);
  void chrome.storage.local.set({ [key]: Object.fromEntries(entries) });
}

/** Local per-row activity watermark for the read-only list watcher. */
type ListWatchState = Record<string, string>;

const listWatchStorageKey = (accountDisplayName: string) =>
  `boss-list-watch:${accountDisplayName}`;

async function readListWatchState(key: string): Promise<ListWatchState> {
  if (typeof chrome === "undefined" || !chrome.storage?.local) return {};
  return new Promise((resolve) => {
    chrome.storage.local.get(key, (value) => {
      const state = value?.[key];
      resolve(state && typeof state === "object" ? state as ListWatchState : {});
    });
  });
}

function writeListWatchState(key: string, state: Map<string, string>) {
  if (typeof chrome === "undefined" || !chrome.storage?.local) return;
  // Only row identity -> last seen activity label, bounded like the unread
  // watermark. No candidate text beyond what BOSS already renders on the row.
  const entries = [...state.entries()].slice(-500);
  void chrome.storage.local.set({ [key]: Object.fromEntries(entries) });
}

export function bossRowMatchesCandidate(
  rowText: string,
  candidateDisplayName: string,
  jobDisplayName: string,
): boolean {
  const compact = (value: string) => value.replace(/\s+/g, "").toLowerCase();
  const row = compact(rowText);
  const candidate = compact(candidateDisplayName);
  const job = compact(jobDisplayName);
  return !!candidate && row.includes(candidate) && (!job || row.includes(job));
}

export class PageController {
  private panel = new PanelController();
  private runId = 0;
  private activePage = false;
  private lastFingerprint = "";
  private initialized = false;
  private development = false;
  private lastObservedSync = "";
  private syncInFlight: Promise<boolean> | null = null;
  private snapshotInFlight: Promise<void> = Promise.resolve();
  private catchupEnabled = true;
  private catchupRunning = false;
  private catchupInterrupted = false;
  private catchupRestartTimer: number | undefined;
  private catchupRetryTimer: number | undefined;
  private catchupIntervalTimer: number | undefined;
  // A missed-window sweep only starts once the page has been idle this long, so
  // opening BOSS to answer a message never turns into a background traversal
  // under the recruiter's hands.
  private autoSweepTimer: number | undefined;
  private lastUserActivityAt = Date.now();
  private catchupRetryAttempts = 0;
  private retryHistorySnapshot = false;
  private stopVisibilityWatch: (() => void) | undefined;
  /** Read-only list watcher: interval, per-row activity watermark, in-flight flag. */
  private listWatchTimer: number | undefined;
  private listWatchWatermark = new Map<string, string>();
  private listWatchStorageKey = "";
  private listWatchScanning = false;
  private pendingCatchupSyncReason: "UNREAD_CANDIDATE_OPENED" | "CATCHUP_RECONCILED" | "HISTORY_SNAPSHOT" | "CANDIDATE_OPENED" = "CANDIDATE_OPENED";
  private stopped = false;
  private catchupUnreadState = new Map<string, boolean>();
  private pendingUnreadRowText = "";
  private pendingUnreadClickExpiresAt = 0;
  /** One panel render per pushed alert version; the channel may retry. */
  private shownLiveAlerts = new Set<string>();
  /** Stable per-job idempotency ids, resolved before any concurrent request. */
  private pendingSyncEventIds = new Map<string, string>();
  constructor(
    private adapter: RecruitmentSiteAdapter,
    private requestTimeoutMs = 8_000,
  ) {}

  private async fields(): Promise<ResolvedFields | null> {
    const [c, j, account] = await Promise.all([
      this.adapter.extractCandidate(),
      this.adapter.extractJob(),
      this.adapter.extractAccount(),
    ]);
    if (c.status === "ERROR" || j.status === "ERROR" || account.status === "ERROR") return null;
    // The page account is the BOSS-side identity that the user is currently
    // operating. The API authenticates the device and verifies this name is
    // mapped to that recruiter; avoiding a blocking /plugin/me round-trip
    // keeps duplicate checks available even while the binding endpoint is
    // slow or temporarily unavailable.
    const boundName = account.value.displayName;
    if (!boundName) return null;
    const nativeCommunications = (c.value.nativeCommunications ?? []).filter(
      (item) => item.recruiterName !== boundName,
    );
    const nativeFingerprint = nativeCommunications
      .map(
        (item) => `${item.recruiterName}|${item.jobName}|${item.contactedAt}`,
      )
      .sort()
      .join(";");
    return {
      accountDisplayName: boundName,
      candidateDisplayName: c.value.displayName,
      candidateAge: c.value.age ?? null,
      candidateExperience: c.value.experience ?? null,
      candidateEducation: c.value.education ?? null,
      jobDisplayName: j.value.displayName,
      platformCandidateId: c.value.platformCandidateId ?? null,
      conversationStartedAt: c.value.conversationStartedAt ?? null,
      conversationUpdatedAt: c.value.conversationUpdatedAt ?? null,
      hasRecruiterOutbound: c.value.hasRecruiterOutbound ?? false,
      recruitmentStatus: c.value.statusEvidence?.status ?? "沟通中",
      statusEvidence: c.value.statusEvidence?.evidence ?? null,
      statusRuleVersion:
        c.value.statusEvidence?.ruleVersion ?? "boss-status-v1",
      historicalJobs: c.value.historicalJobs ?? [],
      nativeCommunications,
      resumeStatus: c.value.resumeStatus ?? "NONE",
      resumeDownload: c.value.resumeDownload ?? null,
      fingerprint: `${c.value.displayName}\u0000${c.value.age ?? ""}\u0000${c.value.experience ?? ""}\u0000${c.value.education ?? ""}\u0000${j.value.displayName}\u0000${nativeFingerprint}`,
    };
  }

  private leaveCandidatePage() {
    this.activePage = false;
    this.lastFingerprint = "";
    this.runId++;
    this.panel.hide(true);
  }

  /**
   * BOSS paints the left conversation list, the candidate header and the job
   * panel in separate passes. A recruiter who clicks a row and immediately
   * switches can otherwise be captured mid-swap, which merges candidate A's
   * identity with candidate B's job. Reading the pane a second time and
   * requiring both reads to describe the same candidate and job rejects that
   * torn snapshot instead of persisting a record that belongs to nobody.
   */
  private async confirmCandidateIdentity(
    fields: ResolvedFields,
  ): Promise<boolean> {
    const confirmed = await this.fields();
    return (
      !!confirmed &&
      confirmed.candidateDisplayName === fields.candidateDisplayName &&
      confirmed.jobDisplayName === fields.jobDisplayName
    );
  }

  private async handlePageChange(
    force = false,
    syncReason = "CANDIDATE_OPENED",
    expectedRowText = "",
  ): Promise<boolean> {
    if (this.stopped) return false;
    if (!this.adapter.isCandidateConversationPage()) {
      this.leaveCandidatePage();
      return false;
    }
    // Claim the run before the asynchronous extraction. Two rapid clicks can
    // otherwise resolve out of order: the slower extraction used to bump
    // runId last and win even though the recruiter had already switched away.
    const id = ++this.runId;
    const fields = await this.fields();
    if (id !== this.runId) return false;
    if (!this.adapter.isCandidateConversationPage()) {
      this.leaveCandidatePage();
      return false;
    }
    if (!this.activePage) {
      this.activePage = true;
      if (this.development)
        this.panel.showDevelopmentStatus(
          `运行中 v${chrome.runtime.getManifest?.().version ?? "0.5.0"}；正在检查并补同步`,
        );
    }
    if (!fields) return false;
    if (expectedRowText && !bossRowMatchesCandidate(
      expectedRowText,
      fields.candidateDisplayName,
      fields.jobDisplayName,
    )) return false;
    const same = !force && fields.fingerprint === this.lastFingerprint;
    if (same) {
      // The same candidate stays on screen, but the conversation itself can
      // move under it: the recruiter answers from a phone or another browser,
      // or the candidate writes back. That is a new event and needs its own
      // duplicate check — the switch-time lookup judged the page as it was
      // back then. Repaints of an unchanged conversation keep their single
      // check, which is what `observedSyncKey` separates.
      const moved = this.observedSyncKey(fields, syncReason) !== this.lastObservedSync;
      const synced = await this.syncObserved(fields, syncReason);
      if (!moved || this.stopped || !this.adapter.isCandidateConversationPage())
        return synced;
      // The sync keeps the incoming reason so the deduplication key stays
      // stable; the lookup is labelled with what actually happened here.
      const response = await this.send(
        "CHECK_CONTEXT",
        this.payload(fields, fields.jobDisplayName, "CONVERSATION_UPDATED"),
      );
      if (this.stopped || !this.adapter.isCandidateConversationPage()) return synced;
      if (response.ok) this.showResult(response.data as ContextResponse, "会话有新动态；未发现其他同事记录");
      return synced;
    }
    // Never persist a mid-switch snapshot.
    if (!(await this.confirmCandidateIdentity(fields))) return false;
    if (id !== this.runId || !this.adapter.isCandidateConversationPage())
      return false;
    if (this.development)
      this.panel.showDevelopmentStatus("正在检查其他招聘人员的跟进记录…");
    // A click must persist even when the independent Feishu duplicate lookup
    // is slow or temporarily unavailable. Run both operations concurrently;
    // only the lookup controls the warning surface.
    const syncTask = this.syncObserved(fields, syncReason);
    const response = await this.send("CHECK_CONTEXT", this.payload(fields, fields.jobDisplayName, syncReason));
    if (id !== this.runId || !this.adapter.isCandidateConversationPage())
      return false;
    // Collection, synchronization and lookup failures are intentionally
    // silent in the page. The in-page surface is reserved exclusively for
    // actionable duplicate evidence; binding/network details remain in the
    // extension popup and diagnostics.
    if (!response.ok) {
      // Do not mark a failed lookup as checked.  The same candidate can stay
      // selected while the network recovers, so clear the fingerprint and
      // retry on the next observation instead of silently allowing a send.
      this.lastFingerprint = "";
      if (!force) this.panel.showLookupUnavailable(this.lookupFailureDetail(response.error));
      return syncTask;
    }
    const data = response.data as ContextResponse;
    // Only a successful lookup establishes the fingerprint.  A transient
    // failure must remain retryable while the recruiter is on this candidate.
    this.lastFingerprint = fields.fingerprint;
    const synchronized = await syncTask;
    // Synchronization may include a screenshot attempt and outlive the page
    // selection that started it. Never let that stale completion reopen a
    // duplicate panel after the recruiter has left or switched candidates.
    if (id !== this.runId || !this.adapter.isCandidateConversationPage())
      return false;
    this.showResult(
      data,
      "候选人已同步；未发现其他同事记录",
    );
    return synchronized;
  }

  /** True while focus sits in a composer, so a pass must not start. */
  private userIsTyping(): boolean {
    if (typeof document === "undefined") return false;
    return document.activeElement instanceof HTMLElement &&
      !!document.activeElement.closest(
        'textarea,[contenteditable="true"],[role="textbox"]',
      );
  }

  /** Name of the conversation currently open, if the page exposes one. */
  private async currentOpenCandidateName(): Promise<string> {
    const observed = await this.fields();
    return observed?.candidateDisplayName?.trim() ?? "";
  }

  /**
   * Re-open the conversation the recruiter was reading before a polling pass.
   *
   * `runBossCatchup` already tries this from the list, but a virtualized row
   * may not be mounted after the scroll reset, and the candidate could have
   * been moved between filters. Confirm by re-reading the page and retry once
   * from the top of the list before giving up.
   */
  async restoreOpenCandidate(name: string): Promise<void> {
    if (!name || this.stopped || this.catchupInterrupted) return;
    const alreadyOpen = async () => {
      const observed = await this.fields();
      return observed?.candidateDisplayName === name;
    };
    if (await alreadyOpen()) return;
    for (let attempt = 0; attempt < 2; attempt++) {
      const item = [...document.querySelectorAll<HTMLElement>("div,li,a")]
        .filter((node) => (node.innerText || "").includes(name))
        .filter((node) => {
          const rect = node.getBoundingClientRect();
          const text = (node.innerText || "").replace(/\s+/g, " ").trim();
          // Same geometry filter the traversal uses for list rows, so a chat
          // bubble that happens to contain the name is never clicked.
          return text.length >= 6 && text.length <= 420 &&
            rect.width >= 160 && rect.width < window.innerWidth * 0.42 &&
            rect.left < window.innerWidth * 0.48 && rect.height >= 38;
        })
        .sort((a, b) => {
          const area = (node: HTMLElement) => {
            const rect = node.getBoundingClientRect();
            return rect.width * rect.height;
          };
          return area(a) - area(b);
        })[0];
      if (!item) break;
      item.click();
      await delay(700);
      if (await alreadyOpen()) return;
    }
  }

  /**
   * Say which failure actually happened. Every failed lookup used to render
   * “无法读取飞书记录”, which blamed Feishu for expired logins, network blips
   * and server restarts as well.
   */
  private lookupFailureDetail(error?: string): string {
    const code = String(error || "");
    if (/DEVICE_AUTH_REQUIRED|INVALID_TOKEN|ACCOUNT_DISABLED|AUTH_REQUIRED/.test(code)) {
      return "登录已失效，请在扩展弹窗中重新使用飞书登录";
    }
    if (/EXTENSION_LOGGED_OUT|EXTENSION_CONTEXT_INVALIDATED/.test(code)) {
      return "当前页面已与扩展断开，请刷新 BOSS 页面";
    }
    if (/REQUEST_TIMEOUT|TIMEOUT/.test(code)) {
      return "请求超时，可能是网络较慢；稍后会自动重试";
    }
    if (/FEISHU_LOOKUP_UNAVAILABLE|503/.test(code)) {
      return "无法读取飞书记录，请稍后重试";
    }
    return code ? `查重请求失败（${code}），稍后会自动重试` : "查重请求失败，稍后会自动重试";
  }

  private showResult(data: ContextResponse, emptyMessage: string) {
    if (data.matches.length) this.panel.show(data);
    else if (this.development) this.panel.showDevelopmentStatus(emptyMessage);
    else this.panel.hide();
  }

  private payload(
    fields: ResolvedFields,
    jobDisplayName = fields.jobDisplayName,
    syncReason?: string,
  ) {
    return {
      platform: this.adapter.platform,
      page_url: location.href,
      platform_candidate_id: fields.platformCandidateId,
      platform_id_scope: "UNKNOWN",
      candidate_display_name: fields.candidateDisplayName,
      candidate_age: fields.candidateAge,
      candidate_experience: fields.candidateExperience,
      candidate_education: fields.candidateEducation,
      job_display_name: jobDisplayName,
      account_display_name: fields.accountDisplayName,
      conversation_started_at: fields.conversationStartedAt,
      conversation_updated_at: fields.conversationUpdatedAt,
      native_communications: fields.nativeCommunications.map((item) => ({
        recruiter_name: item.recruiterName,
        job_name: item.jobName,
        contacted_at: item.contactedAt,
        source: item.source,
      })),
      observed_at: new Date().toISOString(),
      client_event_id: crypto.randomUUID(),
      extractor_version: `${this.adapter.platform}-adapter-resilient-10`,
      recruitment_status: fields.recruitmentStatus,
      status_evidence: fields.statusEvidence,
      status_rule_version: fields.statusRuleVersion,
      ...(syncReason ? { sync_reason: syncReason } : {}),
    };
  }

  /**
   * Deduplication key for one observation of the open conversation.
   *
   * It moves when the conversation itself moves — a new chat timestamp, a new
   * job row, a status change — and stays put for the DOM churn that repaints
   * the same page. Both the sync and the duplicate check use it, so "something
   * happened in this conversation" has exactly one definition.
   */
  private observedSyncKey(fields: ResolvedFields, syncReason: string): string {
    const jobs = [
      ...new Set([fields.jobDisplayName, ...fields.historicalJobs]),
    ].filter(Boolean);
    return `${fields.fingerprint}\u0000${jobs.slice().sort().join("|")}\u0000${fields.conversationUpdatedAt ?? "opened"}\u0000${fields.recruitmentStatus}\u0000${syncReason}`;
  }

  private async syncObserved(
    fields: ResolvedFields,
    syncReason = "CANDIDATE_OPENED",
    listActivityAt?: string,
  ): Promise<boolean> {
    if (!fields.accountDisplayName) return true;
    const jobs = [
      ...new Set([fields.jobDisplayName, ...fields.historicalJobs]),
    ].filter(Boolean);
    const key = this.observedSyncKey(fields, syncReason);
    if (key === this.lastObservedSync) {
      if (this.syncInFlight) return this.syncInFlight;
      return true;
    }
    this.lastObservedSync = key;
    const task = this.performObservedSync(fields, jobs, key, syncReason, listActivityAt);
    this.syncInFlight = task;
    try {
      return await task;
    } finally {
      if (this.syncInFlight === task) this.syncInFlight = null;
    }
  }

  private async performObservedSync(
    fields: ResolvedFields,
    jobs: string[],
    key: string,
    syncReason: string,
    listActivityAt?: string,
  ): Promise<boolean> {
    // Resolve every idempotency id before the first request. `digest` is async,
    // and awaiting it inside the loop let a concurrent CHECK_CONTEXT resolve
    // first, which produced out-of-order observations and a stale page state.
    const eventIds = new Map<string, string>();
    const sourceIds: string[] = [];
    let snapshotRequested = false;
    for (const job of jobs) {
      const eventKey = `${key}\u0000${job}`;
      const existing = this.pendingSyncEventIds.get(eventKey);
      if (existing) {
        eventIds.set(job, existing);
        continue;
      }
      const created = `scan-${await this.digest(eventKey)}`;
      this.pendingSyncEventIds.set(eventKey, created);
      eventIds.set(job, created);
    }
    for (const job of jobs) {
      const result = await this.syncObservedJob(
        fields,
        job,
        key,
        syncReason,
        listActivityAt,
        eventIds.get(job),
      );
      if (!result) {
        this.lastObservedSync = "";
        return false;
      }
      if (result.candidate_source_id) sourceIds.push(result.candidate_source_id);
      snapshotRequested = snapshotRequested || result.snapshot_needed === true;
    }
    // A historical pass captures every read conversation once after all of its
    // job rows have synchronized. Outside that pass the server asks for exactly
    // one capture: the first image this candidate row ever gets. Skip it while
    // the recruiter is typing a reply, and never duplicate the sweep's own
    // capture (it opens every read row and captures there).
    const captureNow =
      sourceIds.length > 0 &&
      (syncReason === "HISTORY_SNAPSHOT" ||
        (snapshotRequested && !this.userIsTyping() && !this.catchupRunning));
    if (captureNow) {
      await this.uploadSnapshot(
        [...new Set(sourceIds)],
        fields.fingerprint,
        {
          candidateDisplayName: fields.candidateDisplayName,
          platformCandidateId: fields.platformCandidateId,
          jobDisplayName: fields.jobDisplayName,
        },
      );
    }
    return true;
  }

  private async handleCatchupCandidate(
    rowText: string,
    listActivityAt: string,
  ): Promise<boolean> {
    // Clicking a virtualized BOSS row changes the route and paints the chat
    // pane asynchronously.  Do not accept a successfully extracted *stale*
    // detail pane: the old implementation did that and advanced the scan
    // checkpoint even though the requested row never opened.
    // BOSS can take several seconds to hydrate the profile/chat pane after a
    // list-row click (especially after a phone-side update). Keep polling the
    // candidate/job identity long enough to avoid treating a slow render as a
    // failed scan and silently skipping the reconciliation.
    for (let attempt = 0; attempt < 24; attempt++) {
      if (this.catchupInterrupted) return false;
      const observed = await this.fields();
      const openedRequestedRow = !!observed && bossRowMatchesCandidate(
        rowText,
        observed.candidateDisplayName,
        observed.jobDisplayName,
      );
      if (openedRequestedRow) {
        // Reconciliation and lookup are two separate decisions. The row was
        // opened because its BOSS list time was newer (or it was missing from
        // the table), so the sync must run even when this recruiter has not
        // sent anything — that is exactly how a candidate reply reaches the
        // Feishu table. The duplicate lookup still runs so a record owned by a
        // different BOSS account can notify the current viewer.
        const reason = this.pendingCatchupSyncReason;
        // Reconciliation is authoritative and must reach the server even when
        // the same candidate is already the visible page: the in-page
        // deduplication key exists to swallow duplicate page-change events,
        // not to veto a row this pass decided to sync.
        this.lastObservedSync = "";
        // `observed.conversationUpdatedAt` is the chat pane's time, which can
        // differ from the list row's top-right time. Send the list time as the
        // business timestamp so the stored time matches what was compared,
        // otherwise a newer chat time would immediately re-trigger the row.
        const synced = await this.syncObserved(
          observed,
          reason,
          listActivityAt || undefined,
        );
        // Every row this pass opens gets its own lookup: the page-change check
        // may still be in flight, and a row nobody looked at must not be skipped
        // just because an earlier candidate shared the same page fingerprint.
        const checked = await this.send(
          "CHECK_CONTEXT",
          this.payload(observed, observed.jobDisplayName, reason),
        );
        if (!checked.ok) return false;
        this.showResult(
          checked.data as ContextResponse,
          "历史查重完成；未发现其他同事记录",
        );
        return synced;
      }
      await delay(500);
    }
    return false;
  }

  private async syncObservedJob(
    fields: ResolvedFields,
    job: string,
    key: string,
    syncReason = "CANDIDATE_OPENED",
    listActivityAt?: string,
    clientEventId?: string,
  ) {
    const eventId = clientEventId ?? `scan-${await this.digest(`${key}\u0000${job}`)}`;
    const response = await this.send("SYNC_CONVERSATION", {
      ...this.payload(fields, job),
      client_event_id: eventId,
      // `sent_at` is the BOSS list row's own top-right time when reconciliation
      // opened this row, so the stored conversation time tracks what the
      // reader compared. A row opened from the chat pane uses its chat time.
      sent_at:
        listActivityAt ??
        fields.conversationUpdatedAt ??
        new Date().toISOString(),
      has_recruiter_outbound: fields.hasRecruiterOutbound,
      // Historical outbound bubbles are evidence that the candidate was
      // contacted before; they are not a new send event. Only the message
      // observer is allowed to emit CONVERSATION_UPDATED.
      sync_reason: syncReason,
    });
    if (!response.ok) {
      this.pendingSyncEventIds.delete(job);
      return null;
    }
    return response.data as
      | { candidate_source_id?: string; snapshot_needed?: boolean }
      | undefined;
  }

  private async handleRecruiterMessageSent(event: RecruiterMessageSent) {
    if (this.stopped || !this.initialized || !this.adapter.isCandidateConversationPage())
      return;
    // A human send always wins over the background traversal. Invalidate any
    // page-change response already in flight so it cannot overwrite or cancel
    // the authoritative send flow for the currently visible candidate.
    this.catchupInterrupted = true;
    this.runId++;
    if (this.catchupRestartTimer !== undefined)
      window.clearTimeout(this.catchupRestartTimer);
    let fields: ResolvedFields | null = null;
    for (let attempt = 0; !fields && attempt < 10; attempt++) {
      fields = await this.fields();
      if (!fields) await delay(150);
    }
    if (!fields || !this.adapter.isCandidateConversationPage()) return;
    this.lastFingerprint = fields.fingerprint;
    const id = ++this.runId;
    if (this.development)
      this.panel.showDevelopmentStatus("招聘消息已发送，正在实时查重…");
    const extracted = this.payload(fields);
    const sentStatus = event.statusEvidence;
    const response = await this.send("MESSAGE_SENT", {
      ...extracted,
      conversation_updated_at: this.latestTime(
        extracted.conversation_updated_at,
        event.sentAt,
      ),
      sent_at: event.sentAt,
      recruitment_status: sentStatus?.status ?? fields.recruitmentStatus,
      status_evidence: sentStatus?.evidence ?? fields.statusEvidence,
      status_rule_version:
        sentStatus?.ruleVersion ?? fields.statusRuleVersion,
      // Present only when the interview scheduler could be read; the server
      // stores an interview row solely from a fully resolved schedule.
      ...(event.interview ? { interview: event.interview } : {}),
    });
    if (id !== this.runId || !this.adapter.isCandidateConversationPage())
      return;
    if (!response.ok) {
      if (this.development)
        this.panel.showDevelopmentStatus(
          "消息已发送；登记失败，已交给后台重试",
        );
      else this.panel.hide();
      return;
    }
    const data = response.data as ContextResponse;
    if (!data.candidate_source_id) {
      // No synced row yet: the send still did its job (real-time duplicate
      // check), and the history pass will create the row with this
      // conversation's times. Nothing to show for a production user.
      if (this.development)
        this.panel.showDevelopmentStatus(
          "消息已发送；该候选人尚未同步，记录将由历史补扫建立",
        );
      else this.panel.hide();
      return;
    }
    this.showResult(data, "招聘消息已登记；未发现其他同事跟进");
    // The old checkpoint is intentionally retained when catch-up is
    // interrupted. Resume only after a quiet period; runBossCatchup's own
    // activity guard will pause again if the recruiter is still working.
    // The interrupted pass resumes as the same kind — a sweep must not be
    // refused by the page-open gate that only admits a missed window.
    this.catchupRestartTimer = window.setTimeout(() => {
      this.catchupRestartTimer = undefined;
      void this.startCatchup(this.retryHistorySnapshot).catch(() => this.scheduleCatchupRetry());
    }, 30_000);
  }

  private async initialize() {
    const auth = await this.send("GET_AUTH", {});
    if (this.stopped) return;
    const state = auth.ok
      ? (auth.data as
          { apiBaseUrl?: string; catchupEnabled?: boolean } | undefined)
      : undefined;
    const base = state?.apiBaseUrl || "";
    this.catchupEnabled = state?.catchupEnabled !== false;
    this.development =
      /^http:\/\/(localhost|127\.0\.0\.1)(?::\d+)?(?:\/|$)/.test(base);
    this.initialized = true;
    // No unattended pass runs merely because the page opened. This call only
    // does something when the 23:30 sweep window was missed while the browser
    // was closed; otherwise it returns immediately. The scheduled window and
    // the popup's manual restart are the only other traversals.
    void this.startCatchup().catch(() => this.scheduleCatchupRetry());
    // Keep an open BOSS tab useful after phone-side conversations, but only
    // while it is actually visible: a background tab refreshes right before
    // the recruiter returns to it.
    this.observeVisibility();
    this.scheduleCatchup();
    try {
      await this.handlePageChange();
    } catch {
      this.leaveCandidatePage();
    }
    // Settings are operationally optional. Do not hold the first duplicate
    // check (or message observer) hostage to a slow/unavailable settings
    // endpoint; apply the server value when it arrives and only then decide
    // whether the background catch-up pass should start.
    void this.send("GET_PLUGIN_SETTINGS", {})
      .then((remote) => {
        if (this.stopped) return;
        if (remote?.ok) {
          const enabled = (remote.data as { catchup_enabled?: boolean } | undefined)
            ?.catchup_enabled;
          if (typeof enabled === "boolean") {
            const wasEnabled = this.catchupEnabled;
            this.catchupEnabled = enabled;
            // GET_AUTH is a local cache and can still contain yesterday's
            // company switch. When the live server setting enables catch-up,
            // offer one pass now instead of waiting for a reload — it still
            // only runs when the 23:30 window was missed.
            if (enabled && !wasEnabled)
              void this.startCatchup().catch(() => this.scheduleCatchupRetry());
          }
        }
      })
      // Start the list watcher once the effective switch is known, whether the
      // request succeeded or not: an unreachable settings endpoint must not
      // cost the recruiter the duplicate check.
      .finally(() => this.syncListWatch());
  }

  /**
   * Keep the list watcher running only while automatic collection is enabled.
   *
   * The watcher is registered here rather than at construction so a page whose
   * company switch is off never leaves a repeating timer behind.
   */
  private syncListWatch(): void {
    if (this.stopped) return;
    if (this.catchupEnabled) this.startListWatch();
    else this.stopListWatch();
  }

  /** True when nobody has typed, clicked, scrolled or focused a composer recently. */
  private idleLongEnough(): boolean {
    return !this.userIsTyping() && Date.now() - this.lastUserActivityAt >= AUTO_SWEEP_QUIET_MS;
  }

  /** Re-check the quiet window later instead of starting a sweep under the cursor. */
  private scheduleAutoSweep(): void {
    if (this.autoSweepTimer !== undefined) window.clearTimeout(this.autoSweepTimer);
    this.autoSweepTimer = window.setTimeout(() => {
      this.autoSweepTimer = undefined;
      if (this.stopped || !this.catchupEnabled || this.catchupRunning) return;
      if (!this.idleLongEnough()) {
        this.scheduleAutoSweep();
        return;
      }
      void this.startCatchup().catch(() => this.scheduleCatchupRetry());
    }, AUTO_SWEEP_RECHECK_MS);
  }

  private async startCatchup(historySnapshot = false) {
    if (this.stopped || this.adapter.platform !== "boss" || !this.catchupEnabled || this.catchupRunning) return;
    this.catchupRunning = true;
    this.retryHistorySnapshot = historySnapshot;
    this.catchupInterrupted = false;
    // The status card is raised only once this call is known to traverse, so a
    // page open that has nothing to do stays completely silent. The activity
    // observer then pauses the pass as soon as the recruiter touches BOSS.
    if (this.catchupRetryTimer !== undefined) {
      window.clearTimeout(this.catchupRetryTimer);
      this.catchupRetryTimer = undefined;
    }
    // Counters live outside the try so the finally block can adapt the cadence
    // from what this pass actually produced.
    let traversed = 0;
    let matched = 0;
    let unreadSkipped = 0;
    let passCompleted = false;
    try {
    // After the popup requests a restart, BOSS reloads the shell with no
    // candidate selected. Do not require candidate/job extraction here: the
    // catch-up traversal will open each eligible conversation and
    // handlePageChange(true) will extract its fields at that point.
    let account = await this.adapter.extractAccount();
    // On a reload BOSS paints the shell in stages.  The content script can
    // start before the top-right account identity exists, so wait for that
    // stable account-level signal just as the traversal waits for the list.
    for (let attempt = 0; account.status === "ERROR" && attempt < 30; attempt++) {
      await delay(500);
      account = await this.adapter.extractAccount();
    }
    if (account.status === "ERROR" || !account.value.displayName) {
      this.scheduleCatchupRetry();
      return;
    }
    const accountDisplayName = account.value.displayName;
    const unreadStorageKey = `boss-catchup-unread:${accountDisplayName}`;
    const historyStorageKey = catchupHistoryKey(accountDisplayName);
    const persistedUnread = await readUnreadState(unreadStorageKey);
    this.catchupUnreadState = new Map(Object.entries(persistedUnread));
    const hasWatermarkStore = typeof chrome !== "undefined" && !!chrome.storage?.local;
    const historyState = hasWatermarkStore ? await readCatchupHistory(historyStorageKey) : {};
    // Today is the scope: the anchor is the last day a finished sweep covered,
    // so a sweep costs the conversations whose list activity falls on a day the
    // anchor does not cover — today alone in the normal case, never the size of
    // the whole list.
    const anchorDay = sweepAnchorDay(historyState);
    const anchor = dateKeyString(anchorDay);
    // Leaving the page open has to be enough. A sweep window that passed while
    // the browser was closed (or while the recruiter worked in another app)
    // would otherwise wait for the next 23:30 slot, so this visit upgrades
    // itself to the history pass — with exactly the same scope as the scheduled
    // one: today's conversations only. Without a usable local watermark store
    // the visit does nothing rather than sweeping blindly.
    //
    // Nothing else runs unattended: the light per-open reconcile was removed
    // because it traversed the list on every page load and, being a re-read of
    // rows the recruiter had already seen, only produced status noise. Only the
    // 23:30 window, a manual restart, or this make-up pass traverse now.
    if (!historySnapshot) {
      if (!pageOpenSweepDue(historyState, hasWatermarkStore)) return;
      // Only sweep a page the recruiter is not using. Any typing, click or
      // scroll postpones it; scheduling a deferred attempt keeps the promise
      // that leaving the tab open is enough.
      if (!this.idleLongEnough()) {
        this.scheduleAutoSweep();
        return;
      }
      historySnapshot = true;
      this.retryHistorySnapshot = true;
    }
    if (historySnapshot) {
      writeCatchupHistory(historyStorageKey, { last_attempt_at: new Date().toISOString() });
    }
    // Past this point the pass really traverses: tell the recruiter, in the
    // same bottom-right surface used for duplicate results.
    this.panel.showCatchupStatus();
    // Polling is deliberately anchor-free: every pass walks the whole
    // “沟通中” list and prices each row against the stored table on its own.
    // An account-wide watermark is what previously let a row's newer time be
    // consumed without ever reaching Feishu.
    const indexResponse = await this.send("GET_CONVERSATION_INDEX", {
      platform: "boss",
      account_display_name: accountDisplayName,
    });
    const indexItems = indexResponse.ok
      ? ((indexResponse.data as {
          items?: Array<{
            candidate_display_name: string;
            job_display_name: string;
            conversation_updated_at: string;
            created_at?: string;
            synced?: boolean;
            recruiter_account?: string;
          }>;
        } | undefined)?.items ?? [])
      : [];
    const compact = (value: string) => value.replace(/\s+/g, "").toLowerCase();
    // The row key is shared with the read-only list watcher, so both agree on
    // what "the same row" means across passes.
    const rowIdentity = (rowText: string) => bossRowIdentity(rowText);
    const indexEntryFor = (rowText: string) => {
      const row = compact(rowText);
      return indexItems.find((item) => {
        const candidate = compact(item.candidate_display_name);
        const job = compact(item.job_display_name);
        return candidate.length > 0 && row.includes(candidate) && (!job || row.includes(job));
      });
    };
    const unreadStateKey = (rowText: string) => {
      const match = indexEntryFor(rowText);
      return `${accountDisplayName}\u0000${match ? `${compact(match.candidate_display_name)}\u0000${compact(match.job_display_name)}` : rowIdentity(rowText)}`;
    };
    const previousUnread = this.catchupUnreadState;
    const observedUnread = new Map<string, boolean>();
    const unreadChanged = (rowText: string) => {
      const key = unreadStateKey(rowText);
      const current = hasBossUnreadBadge(rowText);
      observedUnread.set(key, current);
      return previousUnread.get(key) === true && !current;
    };
    // Every row newer than the anchor is opened, synchronized and snapshotted.
    // Unread rows are the only exception because clicking them would consume the
    // recruiter's unread marker.
    const shouldOpen = (rowText: string, activity: string) => {
      traversed += 1;
      unreadChanged(rowText);
      // A sweep covers the days the anchor does not already cover. Rows from a
      // covered day stay untouched even when they share a rendered batch with
      // today's rows, which is how yesterday's conversations used to be swept.
      if (historySnapshot && !isWithinSweepScope(activity, anchorDay)) {
        this.pendingCatchupSyncReason = "CANDIDATE_OPENED";
        return false;
      }
      const decision = decideBossRow(rowText, activity, indexEntryFor(rowText), historySnapshot);
      if (decision.skip === "UNREAD") unreadSkipped += 1;
      if (!decision.open) {
        this.pendingCatchupSyncReason = "CANDIDATE_OPENED";
        return false;
      }
      this.pendingCatchupSyncReason = decision.reason;
      matched += 1;
      return true;
    };
    const scanStartedAt = new Date().toISOString();
    // Remember where the recruiter was reading. A pass switches to “沟通中”
    // and clicks through rows, so without this the tab is left on whichever
    // conversation the traversal happened to touch last.
    const originalCandidate = await this.currentOpenCandidateName();
    const originalFilter = currentBossListFilter();
    // Never start while the recruiter is typing a reply. A pass switches
    // filters and clicks list rows; running it under someone's cursor is how a
    // tool ends up "helping" at exactly the wrong moment.
    if (this.userIsTyping()) {
      this.scheduleCatchupRetry();
      return;
    }
    // BOSS initially opens the broad “全部候选人” scope. Polling must first
    // enter the top-level “沟通中” list before it can price any row.
    if (!(await openBossCommunicatingFilter())) {
      this.scheduleCatchupRetry();
      return;
    }

    const result = await runBossCatchup(
      anchor,
      originalCandidate,
      async (activity, rowText) => {
        const completed = await this.handleCatchupCandidate(rowText, activity);
        if (completed && !this.stopped)
          this.panel.showCatchupStatus(
            `本轮已处理 ${matched} 个会话（范围：昨天和今天）`,
          );
        return completed;
      },
      shouldOpen,
      ({ rowText, hasUnread }) => {
        observedUnread.set(unreadStateKey(rowText), hasUnread);
      },
      () => this.stopped || this.catchupInterrupted,
    );
    if (this.stopped) return;
    // Put the recruiter back: the list they were reading first (the candidate's
    // row only exists there), then the conversation they were reading. When the
    // original list could not be identified the restore falls back to 新招呼 —
    // ending a pass on the traversal's own “沟通中” list leaves the recruiter
    // staring at a list the tool picked.
    await restoreBossListFilter(originalFilter);
    await this.restoreOpenCandidate(originalCandidate);
    this.catchupUnreadState = observedUnread;
    writeUnreadState(unreadStorageKey, observedUnread);
    if (result.available && result.complete && isSuspiciousEmptyPass(result, traversed)) {
      // Do not record an empty list as a finished sweep: report it, keep the
      // watermark owed and let the retry schedule try again.
      await this.reportCatchupIssue("CATCHUP_EMPTY_LIST");
      await this.send("REPORT_SCAN_COMPLETED", {
        platform: "boss",
        account_display_name: accountDisplayName,
        completed_through_at: scanStartedAt,
        cursor: { traversed, candidates_opened: matched, unread_left_alone: unreadSkipped, empty: true },
      });
      this.scheduleCatchupRetry();
    } else if (result.available && result.complete) {
      this.catchupRetryAttempts = 0;
      passCompleted = true;
      if (historySnapshot) {
        // Only a finished sweep moves the anchor; an interrupted one is retried
        // by the next visit (after the minimum gap). Its own day is covered
        // whole, because a finished sweep has covered its entire scope.
        const sweptThrough = nextSweepAnchor();
        writeCatchupHistory(historyStorageKey, {
          last_completed_at: new Date().toISOString(),
          swept_through_date: sweptThrough,
        });
        historyState.swept_through_date = sweptThrough;
      }
      // Monitoring only: this timestamp never filters candidates any more.
      await this.send("REPORT_SCAN_COMPLETED", {
        platform: "boss",
        account_display_name: accountDisplayName,
        completed_through_at: scanStartedAt,
        cursor: { traversed, candidates_opened: matched, unread_left_alone: unreadSkipped },
      });
    } else {
      // A transient BOSS DOM race or network failure retries automatically;
      // rows already reconciled simply fail their per-row comparison next pass.
      this.scheduleCatchupRetry();
    }
    } finally {
      this.catchupRunning = false;
      // Do not leave a stale status card after a completed or interrupted
      // pass; duplicate warnings are rendered again only when actionable.
      this.panel.hideCatchupStatus();
      // Adapt the cadence only from a pass that actually finished. A partial or
      // failed pass keeps its retry schedule so missed rows are not pushed out
      // behind a backoff earned by an earlier quiet pass.
      if (this.catchupEnabled && !this.catchupInterrupted && passCompleted)
        this.scheduleCatchup();
    }
  }
  /**
   * Render a duplicate alert pushed over the live channel.
   *
   * The page may have been sitting on this candidate since its last check, so
   * without this the recruiter keeps working until the next observation happens
   * to re-detect the clash. The candidate and BOSS account are re-read from the
   * page and must match the alert, so a pushed alert can never be shown against
   * an unrelated conversation or another account open in the same browser.
   */
  async handleLiveAlert(event: {
    lookup_alert_id?: string;
    candidate_name?: string;
    job_name?: string;
    matched_recruiter_id?: string | null;
    matched_recruiter_name?: string;
    match_level?: string;
    match_reason?: string;
    first_contact_at?: string | null;
    last_activity_at?: string | null;
    notification_version?: number;
    own_history?: boolean;
  }): Promise<boolean> {
    if (this.stopped || !this.initialized || !this.adapter.isCandidateConversationPage())
      return false;
    const candidateName = (event.candidate_name || "").trim();
    if (!candidateName || !event.matched_recruiter_name) return false;
    const fields = await this.fields();
    if (!fields || fields.candidateDisplayName !== candidateName) return false;
    const alertKey = `${event.lookup_alert_id ?? ""}:${event.notification_version ?? 1}`;
    if (this.shownLiveAlerts.has(alertKey)) return false;
    this.shownLiveAlerts.add(alertKey);
    // An own-history event is the viewer's own cross-job record resurfacing;
    // wording must say "you yourself", never "another recruiter".
    const ui = event.own_history
      ? {
          severity: "warning",
          title: "你本人跟进过的候选人",
          message: `你本人曾在其他岗位跟进过该候选人，与当前岗位不同，注意区分`,
        }
      : {
          severity: "danger",
          title: "发现重复候选人",
          message: `${candidateName}｜${event.job_name || fields.jobDisplayName} 已被其他 BOSS 账号沟通`,
        };
    this.panel.show({
      candidate_source_id: null,
      result_type: String(event.match_level || "CONFIRMED_DUPLICATE"),
      ui,
      matches: [
        {
          match_level: String(event.match_level || "CONFIRMED_DUPLICATE"),
          candidate_source_id: "",
          recruiter_id: String(event.matched_recruiter_id || ""),
          recruiter_name: String(event.matched_recruiter_name),
          job_id: null,
          job_name: event.job_name || fields.jobDisplayName,
          stage: "沟通中",
          first_contact_at: event.first_contact_at ?? null,
          last_activity_at: event.last_activity_at ?? null,
          updated_at: event.last_activity_at ?? null,
          match_reason: String(event.match_reason || "发现重复候选人证据"),
          ...(event.own_history ? { is_own_history: true } : {}),
        },
      ],
      available_actions: [],
      account_mapping: {},
      job_mapping: {},
    });
    return true;
  }

  /** Run one reconciliation pass immediately (popup "立即检查" action). */
  async runCatchupNow(): Promise<void> {
    this.catchupRetryAttempts = 0;
    if (this.catchupRunning) return;
    await this.startCatchup(true).catch(() => this.scheduleCatchupRetry(true));
  }

  /** Queue the next fixed 23:30 Asia/Shanghai pass. */
  private scheduleCatchup(delayMs = nextCatchupWindowDelayMs()): void {
    if (this.stopped || !this.catchupEnabled) return;
    if (this.catchupIntervalTimer !== undefined)
      window.clearTimeout(this.catchupIntervalTimer);
    this.catchupIntervalTimer = window.setTimeout(() => {
      this.catchupIntervalTimer = undefined;
      if (this.stopped || !this.catchupEnabled) return;
      if (this.catchupRunning) {
        this.scheduleCatchup(CATCHUP_RETRY_DELAY_MS);
        return;
      }
      // A new scheduled window gets its own retry budget.
      this.catchupRetryAttempts = 0;
      void this.startCatchup(true).catch(() => this.scheduleCatchupRetry(true));
    }, delayMs);
  }

  private observeVisibility(): void {
    if (this.stopVisibilityWatch) return;
    const handler = () => {
      if (document.hidden || this.stopped) return;
      // Coming back to the tab re-reads the conversation list once: while it
      // was in the background the recruiter may have answered conversations
      // from a phone or another browser. Visibility changes still never start
      // a catching-up traversal — this is a read, not a pass.
      void this.watchConversationList();
    };
    document.addEventListener("visibilitychange", handler);
    this.stopVisibilityWatch = () =>
      document.removeEventListener("visibilitychange", handler);
  }

  /** Start the read-only list watcher. The callback self-gates on `catchupEnabled`. */
  private startListWatch(): void {
    if (this.listWatchTimer !== undefined) window.clearInterval(this.listWatchTimer);
    this.listWatchTimer = window.setInterval(() => {
      void this.watchConversationList();
    }, LIST_WATCH_INTERVAL_MS);
  }

  private stopListWatch(): void {
    if (this.listWatchTimer === undefined) return;
    window.clearInterval(this.listWatchTimer);
    this.listWatchTimer = undefined;
  }

  /**
   * Run the duplicate check for conversations whose list activity moved.
   *
   * The trigger is a per-row watermark kept on this device: a row whose BOSS
   * activity label moved past the activity this browser last saw for it is a
   * conversation something happened in while nobody was looking here. The
   * check is issued from the row text alone — candidate and job — because the
   * check is read-only and must not open (and therefore read) the
   * conversation. Rows that are newly mounted are recorded silently, so the
   * first scan after an install never fires a check per conversation.
   */
  private async watchConversationList(): Promise<void> {
    if (this.stopped || !this.initialized || !this.catchupEnabled) return;
    if (this.adapter.platform !== "boss" || this.listWatchScanning || this.catchupRunning) return;
    if (!this.adapter.isCandidateConversationPage()) return;
    const rows = readBossMountedRows();
    if (!rows.length) return;
    this.listWatchScanning = true;
    try {
      const account = await this.adapter.extractAccount();
      if (this.stopped || account.status !== "OK" || !account.value.displayName) return;
      const storageKey = listWatchStorageKey(account.value.displayName);
      if (storageKey !== this.listWatchStorageKey) {
        this.listWatchWatermark = new Map(
          Object.entries(await readListWatchState(storageKey)),
        );
        this.listWatchStorageKey = storageKey;
      }
      const openCandidate = this.openCandidateName();
      const advanced: Array<{ rowText: string; activity: string; identity: string }> = [];
      // A virtualized list can mount the same conversation twice; one round
      // must not probe the same row identity twice.
      const seenIdentities = new Set<string>();
      let changed = false;
      for (const row of rows) {
        const identity = bossRowIdentity(row.rowText);
        // A row whose text carried no readable candidate name is not probeable.
        if (identity.startsWith("\u0000") || seenIdentities.has(identity)) continue;
        seenIdentities.add(identity);
        const stored = this.listWatchWatermark.get(identity);
        if (!stored) {
          // First sight: remember the row, check nothing.
          this.listWatchWatermark.set(identity, row.activity);
          changed = true;
          continue;
        }
        if (!isBossListActivityNewer(row.rowText, row.activity, stored)) continue;
        advanced.push({ rowText: row.rowText, activity: row.activity, identity });
      }
      let checks = 0;
      for (const row of advanced) {
        if (this.stopped) return;
        const parsed = parseBossRowIdentity(row.rowText);
        // The conversation the recruiter has open already owns a full check
        // (age, education, BOSS-native colleague records). A row-level check
        // would send less evidence and could replace a confirmed card with a
        // weaker one, so it is not run for that row.
        const skipOpen = !!openCandidate && row.rowText.includes(openCandidate);
        if (skipOpen || !parsed.jobDisplayName) {
          if (skipOpen) {
            this.listWatchWatermark.set(row.identity, row.activity);
            changed = true;
          }
          continue;
        }
        if (checks >= LIST_WATCH_MAX_CHECKS_PER_ROUND) break;
        checks += 1;
        const response = await this.send(
          "CHECK_CONTEXT",
          this.listRowPayload(account.value.displayName, parsed, row.activity),
        );
        if (this.stopped) return;
        if (!response.ok) continue; // Keep the old watermark and retry next round.
        const data = response.data as ContextResponse | undefined;
        // The API ignores a probe that did not come from the accepted chat
        // page. Recording that as seen would swallow the row silently, so it
        // is retried like a failure instead.
        if (data?.result_type === "IGNORED_PAGE") continue;
        this.listWatchWatermark.set(row.identity, row.activity);
        changed = true;
        this.showListWatchResult(data, parsed);
      }
      if (changed) writeListWatchState(storageKey, this.listWatchWatermark);
    } finally {
      this.listWatchScanning = false;
    }
  }

  /**
   * The candidate the page currently shows, from the last completed page check.
   * `lastFingerprint` is built with U+0000 separators and starts with the name.
   */
  private openCandidateName(): string {
    return this.lastFingerprint.split("\u0000")[0] ?? "";
  }

  /** A check probe built from a list row: identity only, no chat evidence. */
  private listRowPayload(
    accountDisplayName: string,
    parsed: { candidateDisplayName: string; jobDisplayName: string },
    activity: string,
  ) {
    return {
      platform: this.adapter.platform,
      page_url: location.href,
      platform_candidate_id: null,
      platform_id_scope: "UNKNOWN",
      candidate_display_name: parsed.candidateDisplayName,
      candidate_age: null,
      candidate_experience: null,
      candidate_education: null,
      job_display_name: parsed.jobDisplayName,
      account_display_name: accountDisplayName,
      conversation_started_at: null,
      conversation_updated_at: activity,
      native_communications: [],
      observed_at: new Date().toISOString(),
      client_event_id: crypto.randomUUID(),
      extractor_version: `${this.adapter.platform}-list-watch-1`,
      sync_reason: LIST_WATCH_SYNC_REASON,
    };
  }

  private showListWatchResult(
    data: ContextResponse | undefined,
    parsed: { candidateDisplayName: string; jobDisplayName: string },
  ) {
    if (!data) return;
    if (!data.matches?.length) {
      if (this.development)
        this.panel.showDevelopmentStatus(
          `${parsed.candidateDisplayName}｜${parsed.jobDisplayName} 有新动态；未发现其他同事记录`,
        );
      return;
    }
    this.panel.show(
      data,
      `沟通列表检测到新动态，已自动查重：${parsed.candidateDisplayName}｜${parsed.jobDisplayName}`,
    );
  }

  private scheduleCatchupRetry(historySnapshot = this.retryHistorySnapshot) {
    if (this.stopped || !this.catchupEnabled || this.catchupRetryTimer !== undefined) return;
    if (this.catchupRetryAttempts >= CATCHUP_MAX_RETRIES) {
      this.scheduleCatchup();
      return;
    }
    this.catchupRetryAttempts += 1;
    this.catchupRetryTimer = window.setTimeout(() => {
      this.catchupRetryTimer = undefined;
      void this.startCatchup(historySnapshot).catch(() => this.scheduleCatchupRetry(historySnapshot));
    }, CATCHUP_RETRY_DELAY_MS);
  }
  stopCatchup() {
    this.runId++;
    this.stopped = true;
    this.catchupEnabled = false;
    this.catchupInterrupted = true;
    if (this.catchupIntervalTimer !== undefined) window.clearTimeout(this.catchupIntervalTimer);
    if (this.catchupRetryTimer !== undefined) window.clearTimeout(this.catchupRetryTimer);
    if (this.catchupRestartTimer !== undefined) window.clearTimeout(this.catchupRestartTimer);
    if (this.autoSweepTimer !== undefined) window.clearTimeout(this.autoSweepTimer);
    this.stopListWatch();
    this.stopVisibilityWatch?.();
    this.stopVisibilityWatch = undefined;
    this.catchupIntervalTimer = undefined;
    this.catchupRetryTimer = undefined;
    this.catchupRestartTimer = undefined;
    this.autoSweepTimer = undefined;
    this.panel.hideCatchupStatus();
    this.panel.dispose();
  }
  start() {
    // Track real activity so an automatic sweep can wait for a quiet page.
    const stopActivityWatch = observeUserActivity(() => {
      this.lastUserActivityAt = Date.now();
    });
    const stopPage = this.adapter.observePageChange(() => {
      if (!this.initialized) return;
      const unreadPending = Date.now() <= this.pendingUnreadClickExpiresAt;
      const reason = unreadPending ? "UNREAD_CANDIDATE_OPENED" : "CANDIDATE_OPENED";
      const expected = unreadPending ? this.pendingUnreadRowText : "";
      void this.handlePageChange(false, reason, expected).then((completed) => {
        if (completed && unreadPending && expected === this.pendingUnreadRowText) {
          this.pendingUnreadRowText = "";
          this.pendingUnreadClickExpiresAt = 0;
        }
      });
    });
    const stopUnreadClick = this.adapter.platform === "boss"
      ? observeBossUnreadConversationClick((rowText) => {
          if (this.catchupRunning) return;
          this.pendingUnreadRowText = rowText;
          this.pendingUnreadClickExpiresAt = Date.now() + 15_000;
        })
      : () => {};
    const stopMessages = this.adapter.observeRecruiterMessageSent(
      (event) => void this.handleRecruiterMessageSent(event),
    );
    const stopResume =
      this.adapter.observeResumePreviewOpened?.(
        (event) => void this.handleResumePreview(event),
      ) ?? (() => {});
    void this.initialize();
    return () => {
      stopPage();
      stopUnreadClick();
      stopMessages();
      stopResume();
      stopActivityWatch();
      this.stopListWatch();
      this.stopVisibilityWatch?.();
      this.stopVisibilityWatch = undefined;
      if (this.catchupIntervalTimer !== undefined) {
        window.clearInterval(this.catchupIntervalTimer);
        this.catchupIntervalTimer = undefined;
      }
      if (this.autoSweepTimer !== undefined) {
        window.clearTimeout(this.autoSweepTimer);
        this.autoSweepTimer = undefined;
      }
    };
  }
  private async handleResumePreview(event: {
    url?: string;
    fileName?: string;
    target?: HTMLElement;
  }) {
    if (this.stopped) return;
    if (!this.initialized || !this.adapter.isCandidateConversationPage())
      return;
    const fields = await this.fields();
    const url = event.url || fields?.resumeDownload?.url;
    if (!fields) return;
    const context = await this.send("CHECK_CONTEXT", this.payload(fields));
    const sourceId = (context.data as ContextResponse | undefined)
      ?.candidate_source_id;
    if (!context.ok || !sourceId) return;
    if (url) {
      await this.send("UPLOAD_RESUME_FROM_URL", {
        candidateSourceIds: [sourceId],
        url,
        fileName: event.fileName || fields.resumeDownload?.fileName,
      });
    } else if (event.target) {
      const preview = document.querySelector<HTMLElement>(
        '[role="dialog"], [class*="preview" i], [class*="modal" i]',
      );
      const dataUrl = await captureBossPreviewScreenshot(
        preview || event.target,
      );
      await this.send("UPLOAD_RESUME_SCREENSHOT", {
        candidateSourceIds: [sourceId],
        dataUrl,
        fileName: "candidate-resume-preview.jpg",
      });
    }
  }
  private latestTime(left: string | null, right: string) {
    return !left || Date.parse(right) > Date.parse(left) ? right : left;
  }
  private async digest(value: string) {
    return (await sha256Hex(new TextEncoder().encode(value))).slice(0, 48);
  }
  private async uploadSnapshot(
    candidateSourceIds: Array<string | null>,
    expectedFingerprint?: string,
    expectedCandidate?: {
      candidateDisplayName: string;
      platformCandidateId: string | null;
      jobDisplayName: string;
    },
  ) {
    const sources = candidateSourceIds.filter(
      (value): value is string => !!value,
    );
    if (!sources.length || this.adapter.platform !== "boss") return;
    const task = async () => {
      if (this.stopped) return;
      try {
        this.panel.showSnapshotStatus("正在截取沟通记录…");
        // BOSS acknowledges a send before the outgoing bubble is committed to
        // the virtualized chat list. Let that render settle so the screenshot
        // contains the invitation that triggered this capture.
        // jsdom (used by the unit suite) has no compositor frame API; in the
        // real browser this guard is always true and provides the settling
        // window without making deterministic tests sleep.
        if (
          location.protocol === "https:" &&
          typeof window.requestAnimationFrame === "function"
        )
          await delay(750);
        const snapshot = await captureBossConversationSnapshot();
        // The user may switch candidates while a long historical capture is
        // running. Never attach the captured pixels to the stale source IDs;
        // verify the conversation fingerprint again immediately before the
        // upload.
        const afterCapture = await this.fields();
        const sameCandidate =
          !!afterCapture &&
          !!expectedCandidate &&
          afterCapture.candidateDisplayName === expectedCandidate.candidateDisplayName &&
          afterCapture.platformCandidateId === expectedCandidate.platformCandidateId &&
          afterCapture.jobDisplayName === expectedCandidate.jobDisplayName;
        if (
          !afterCapture ||
          (expectedFingerprint &&
            afterCapture.fingerprint !== expectedFingerprint &&
            !sameCandidate)
        )
          throw new SnapshotCaptureError("SNAPSHOT_CANDIDATE_CHANGED");
        const result = await this.send("UPLOAD_SNAPSHOT", {
          candidateSourceIds: sources,
          parts: snapshot.parts,
          snapshotHash: snapshot.hash,
        });
        if (!result.ok)
          throw new SnapshotCaptureError(
            result.error || "SNAPSHOT_UPLOAD_FAILED",
          );
        this.panel.showSnapshotStatus("沟通截图已上传");
      } catch (error) {
        const code =
          error instanceof SnapshotCaptureError
            ? error.code
            : "SNAPSHOT_CAPTURE_FAILED";
        await this.send("REPORT_SNAPSHOT_STATUS", {
          candidate_source_ids: sources,
          status: code === "SNAPSHOT_INTERRUPTED_BY_USER" ? "INTERRUPTED" : "FAILED",
          error_code: code,
        });
        await this.reportSnapshotIssue(
          code,
        );
        this.panel.showSnapshotStatus("截图失败，已记录诊断；不会重复打扰当前沟通", true);
        // A failed capture is never retried from the page. Automatic
        // re-capture used to fire minutes later while the recruiter was
        // working in the same conversation and hijack it again; the next
        // scheduled history pass is the natural retry point.
      }
    };
    this.snapshotInFlight = this.snapshotInFlight.then(task, task);
    await this.snapshotInFlight;
  }
  /** Report a catch-up problem without leaking page content. */
  private async reportCatchupIssue(errorCode: string) {
    try {
      const diagnostic = await this.adapter.getDiagnostics();
      await this.send("SEND_DIAGNOSTIC", {
        platform: diagnostic.platform,
        adapter_version: diagnostic.adapterVersion,
        page_type: diagnostic.pageType,
        account_status: diagnostic.accountStatus,
        candidate_status: diagnostic.candidateStatus,
        job_status: diagnostic.jobStatus,
        platform_id_status: diagnostic.platformIdStatus,
        error_codes: [...new Set([...diagnostic.errorCodes, errorCode])],
        sanitized_context: { ...diagnostic.sanitizedContext, catchup: true },
      });
    } catch {
      /* Diagnostics must never interrupt candidate synchronization. */
    }
  }

  private async reportSnapshotIssue(errorCode: string) {
    try {
      const diagnostic = await this.adapter.getDiagnostics();
      await this.send("SEND_DIAGNOSTIC", {
        platform: diagnostic.platform,
        adapter_version: diagnostic.adapterVersion,
        page_type: diagnostic.pageType,
        account_status: diagnostic.accountStatus,
        candidate_status: diagnostic.candidateStatus,
        job_status: diagnostic.jobStatus,
        platform_id_status: diagnostic.platformIdStatus,
        error_codes: [...new Set([...diagnostic.errorCodes, errorCode])],
        sanitized_context: {
          ...diagnostic.sanitizedContext,
          snapshotCapture: true,
        },
      });
    } catch {
      /* Diagnostics must never interrupt candidate synchronization. */
    }
  }
  private async send(
    type: string,
    payload: unknown,
  ): Promise<{ ok: boolean; data?: unknown; error?: string }> {
    if (this.stopped) return { ok: false, error: "EXTENSION_LOGGED_OUT" };
    let timer = 0;
    const timeout = new Promise<{ ok: false; error: string }>((resolve) => {
      timer = window.setTimeout(
        () => resolve({ ok: false, error: "REQUEST_TIMEOUT" }),
        this.requestTimeoutMs,
      );
    });
    try {
      return await Promise.race([
        sendRuntimeMessage<{ ok: boolean; data?: unknown; error?: string }>({ type, payload }),
        timeout,
      ]);
    } catch (error) {
      // Reloading the extension invalidates content scripts that are still
      // running in an open BOSS tab. Treat that transient browser error as a
      // failed request; the user can refresh the tab to attach the new script.
      if (String(error).includes("Extension context invalidated"))
        return { ok: false, error: "EXTENSION_CONTEXT_INVALIDATED" };
      throw error;
    } finally {
      window.clearTimeout(timer);
    }
  }
}
