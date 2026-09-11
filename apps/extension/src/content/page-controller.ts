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
  hasBossUnreadBadge,
  isBossListActivityNewer,
  runBossCatchup,
} from "../adapters/boss/boss-catchup";
import { isBossInviteScreenshotTrigger } from "../adapters/boss/boss-status";
import { sha256Hex } from "../shared/sha256";
import { sendRuntimeMessage } from "../shared/runtime-message";
import { delay } from "../shared/delay";

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
  private catchupRetryAttempts = 0;
  private stopped = false;
  private catchupUnreadState = new Map<string, boolean>();
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

  private async handlePageChange(force = false): Promise<boolean> {
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
    const same = !force && fields.fingerprint === this.lastFingerprint;
    if (same) {
      return this.syncObserved(fields);
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
    const syncTask = this.syncObserved(fields);
    const response = await this.send("CHECK_CONTEXT", this.payload(fields));
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
      if (!force) this.panel.showLookupUnavailable();
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

  private showResult(data: ContextResponse, emptyMessage: string) {
    if (data.matches.length) this.panel.show(data);
    else if (this.development) this.panel.showDevelopmentStatus(emptyMessage);
    else this.panel.hide();
  }

  private payload(
    fields: ResolvedFields,
    jobDisplayName = fields.jobDisplayName,
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
    };
  }

  private async syncObserved(fields: ResolvedFields): Promise<boolean> {
    if (!fields.accountDisplayName) return true;
    const jobs = [
      ...new Set([fields.jobDisplayName, ...fields.historicalJobs]),
    ].filter(Boolean);
    const key = `${fields.fingerprint}\u0000${jobs.slice().sort().join("|")}\u0000${fields.conversationUpdatedAt ?? "opened"}\u0000${fields.recruitmentStatus}`;
    if (key === this.lastObservedSync) {
      if (this.syncInFlight) return this.syncInFlight;
      return true;
    }
    this.lastObservedSync = key;
    const task = this.performObservedSync(fields, jobs, key);
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
  ): Promise<boolean> {
    for (const job of jobs) {
      const result = await this.syncObservedJob(fields, job, key);
      if (!result) {
        this.lastObservedSync = "";
        return false;
      }
    }
    // Opening or re-observing a conversation never triggers the historical
    // chat capture. It scrolls the recruiter's chat pane, so it is reserved
    // for the confirmed interview-invitation send path only.
    return true;
  }

  private async handleCatchupCandidate(rowText: string): Promise<boolean> {
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
        if (await this.handlePageChange(true)) return true;
        // Some BOSS layouts keep the route at /web/chat/index even after the
        // detail pane has rendered, so isCandidateConversationPage() can
        // briefly reject a real selection. Identity was already validated
        // against the requested row above; use the same sync path as a page
        // change without allowing an unverified stale pane through.
        if (await this.syncObserved(observed)) return true;
      }
      await delay(500);
    }
    return false;
  }

  private async syncObservedJob(
    fields: ResolvedFields,
    job: string,
    key: string,
  ) {
    const response = await this.send("SYNC_CONVERSATION", {
      ...this.payload(fields, job),
      client_event_id: `scan-${await this.digest(`${key}\u0000${job}`)}`,
      sent_at: fields.conversationUpdatedAt ?? new Date().toISOString(),
      has_recruiter_outbound: fields.hasRecruiterOutbound,
      sync_reason: fields.hasRecruiterOutbound
        ? "CONVERSATION_UPDATED"
        : "CANDIDATE_OPENED",
    });
    if (!response.ok) return null;
    return response.data as { candidate_source_id?: string } | undefined;
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
      this.panel.showDevelopmentStatus("招聘消息已发送，正在登记跟进记录…");
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
      if (this.development)
        this.panel.showDevelopmentStatus(
          `消息已发送但未登记：${data.ui.title}`,
        );
      else this.panel.hide();
      return;
    }
    this.showResult(data, "招聘消息已登记；未发现其他同事跟进");
    // The historical chat capture scrolls the recruiter's conversation pane
    // and takes several seconds per screen, so it is reserved for the one
    // action that changes the hiring stage: handing out an interview
    // invitation. Ordinary sends (chat, resume/contact requests) keep the
    // stage unchanged and must not steal the scroll position.
    if (
      isBossInviteScreenshotTrigger(
        event.messageText,
        sentStatus ?? event.statusEvidence,
      )
    ) {
      void this.uploadSnapshot([data.candidate_source_id], fields.fingerprint, {
        candidateDisplayName: fields.candidateDisplayName,
        platformCandidateId: fields.platformCandidateId,
        jobDisplayName: fields.jobDisplayName,
      });
    }
    // The old checkpoint is intentionally retained when catch-up is
    // interrupted. Resume only after a quiet period; runBossCatchup's own
    // activity guard will pause again if the recruiter is still working.
    this.catchupRestartTimer = window.setTimeout(() => {
      this.catchupRestartTimer = undefined;
      void this.startCatchup().catch(() => this.scheduleCatchupRetry());
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
    // Start catch-up independently of the initially selected conversation.
    // BOSS often renders an empty shell first; a transient extraction error
    // must not prevent the account-level traversal from starting.
    void this.startCatchup().catch(() => this.scheduleCatchupRetry());
    // Keep an open BOSS tab useful after phone-side conversations. The list
    // receives the phone-updated activity timestamps from BOSS, so periodically
    // re-running from the server checkpoint discovers those candidates without
    // requiring the recruiter to click each one.
    if (!this.development && this.catchupIntervalTimer === undefined) {
      this.catchupIntervalTimer = window.setInterval(() => {
        if (!this.catchupEnabled || this.catchupRunning) return;
        void this.startCatchup().catch(() => this.scheduleCatchupRetry());
      }, 5 * 60 * 1000);
    }
    try {
      await this.handlePageChange();
    } catch {
      this.leaveCandidatePage();
    }
    // Settings are operationally optional. Do not hold the first duplicate
    // check (or message observer) hostage to a slow/unavailable settings
    // endpoint; apply the server value when it arrives and only then decide
    // whether the background catch-up pass should start.
    void this.send("GET_PLUGIN_SETTINGS", {}).then((remote) => {
      if (this.stopped) return;
      if (remote?.ok) {
        const enabled = (remote.data as { catchup_enabled?: boolean } | undefined)
          ?.catchup_enabled;
        if (typeof enabled === "boolean") {
          const wasEnabled = this.catchupEnabled;
          this.catchupEnabled = enabled;
          // GET_AUTH is a local cache and can still contain yesterday's
          // company switch.  When the live server setting enables catch-up,
          // start the traversal now instead of waiting for another reload.
          if (enabled && !wasEnabled)
            void this.startCatchup().catch(() => this.scheduleCatchupRetry());
        }
      }
    });
  }

  private async startCatchup() {
    if (this.stopped || this.adapter.platform !== "boss" || !this.catchupEnabled || this.catchupRunning) return;
    this.catchupRunning = true;
    this.catchupInterrupted = false;
    // Catch-up is a short background pass. Keep the user informed in the
    // same bottom-right surface used for duplicate results, and let the
    // activity observer pause the pass as soon as they interact with BOSS.
    this.panel.showCatchupStatus();
    if (this.catchupRetryTimer !== undefined) {
      window.clearTimeout(this.catchupRetryTimer);
      this.catchupRetryTimer = undefined;
    }
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
    const persistedUnread = await readUnreadState(unreadStorageKey);
    this.catchupUnreadState = new Map(Object.entries(persistedUnread));
    const response = await this.send("GET_SCAN_CHECKPOINT", {
      platform: "boss",
      account_display_name: accountDisplayName,
    });
    if (!response.ok) {
      this.scheduleCatchupRetry();
      return;
    }
    const checkpoint = response.data as {
      completed_through_at: string;
      cursor?: Record<string, unknown>;
      historical_rescan?: boolean;
    };
    const watermark = checkpoint.completed_through_at;
    const historicalRescanId =
      checkpoint.cursor && typeof checkpoint.cursor.scan_id === "string"
        ? checkpoint.cursor.scan_id
        : undefined;
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
            recruiter_account?: string;
          }>;
        } | undefined)?.items ?? [])
      : [];
    const compact = (value: string) => value.replace(/\s+/g, "").toLowerCase();
    const rowIdentity = (rowText: string) => {
      const normalized = rowText.replace(/\s+/g, " ").trim();
      const withoutDate = normalized.replace(
        /^\s*(?:\d{1,3}\s+)?(?:昨天|今天|刚刚|\d{1,2}:\d{2}|\d{1,2}月\d{1,2}日|\d{4}[./年-]\d{1,2}[./月-]\d{1,2}\s+)/,
        "",
      );
      const name = withoutDate.match(/^[\u4e00-\u9fff·]{2,20}/)?.[0] ?? withoutDate.slice(0, 20);
      const job = withoutDate.slice(name.length).trim().split(/\s+/)[0] ?? "";
      return `${compact(name)}\u0000${compact(job)}`;
    };
    const unreadStateKey = (rowText: string) => {
      const row = compact(rowText);
      const match = indexItems.find((item) => {
        const candidate = compact(item.candidate_display_name);
        const job = compact(item.job_display_name);
        return candidate.length > 0 && row.includes(candidate) && (!job || row.includes(job));
      });
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
    const shouldOpen = (rowText: string, activity: string) => {
      const row = compact(rowText);
      const currentUnread = hasBossUnreadBadge(rowText);
      // An unread badge means the recruiter has not reviewed the candidate
      // yet. Do not open the row, compare timestamps, or sync it: opening it
      // can consume the badge and create a false "phone-side read" event.
      // The next scan will reconcile the true -> false transition instead.
      if (currentUnread) {
        unreadChanged(rowText);
        return false;
      }
      const matches = indexItems.filter((item) => {
        const candidate = compact(item.candidate_display_name);
        const job = compact(item.job_display_name);
        return candidate.length > 0 && row.includes(candidate) &&
          (!job || row.includes(job));
      });
      // A phone-side read can remove BOSS's unread badge without changing the
      // coarse list date. The transition itself is a reconciliation trigger.
      if (unreadChanged(rowText)) return true;
      // An unknown candidate is compared with the last completed scan anchor,
      // so a recruiter returning after several days still gets the backlog.
      if (!matches.length)
        return Date.parse(activity) > Date.parse(watermark);
      return matches.every((item) =>
        isBossListActivityNewer(
          rowText,
          activity,
          item.conversation_updated_at,
        ),
      );
    };
    const scanStartedAt = new Date().toISOString();
    const result = await runBossCatchup(
      watermark,
      "",
      async (_activity, rowText) => {
        const completed = await this.handleCatchupCandidate(rowText);
        return completed;
      },
      shouldOpen,
      ({ rowText, hasUnread }) => {
        observedUnread.set(unreadStateKey(rowText), hasUnread);
      },
      () => this.stopped || this.catchupInterrupted,
    );
    if (this.stopped) return;
    this.catchupUnreadState = observedUnread;
    writeUnreadState(unreadStorageKey, observedUnread);
    // A checkpoint represents a fully completed traversal. A partial scan
    // deliberately keeps the old watermark so skipped rows are retried.
    if (result.available && result.complete) {
      this.catchupRetryAttempts = 0;
      await this.send("PUT_SCAN_CHECKPOINT", {
        platform: "boss",
        account_display_name: accountDisplayName,
        completed_through_at: scanStartedAt,
        cursor: {
          scanned: result.scanned,
          complete: true,
          ...(historicalRescanId ? { scan_id: historicalRescanId } : {}),
        },
      });
    } else {
      // Keep the old watermark and retry automatically. A transient BOSS DOM
      // race or network failure must not require a page refresh or a manual
      // "历史扫补" click to recover.
      this.scheduleCatchupRetry();
    }
    } finally {
      this.catchupRunning = false;
      // Do not leave a stale status card after a completed or interrupted
      // pass; duplicate warnings are rendered again only when actionable.
      this.panel.hideCatchupStatus();
    }
  }
  private scheduleCatchupRetry() {
    if (this.stopped || !this.catchupEnabled || this.catchupRetryTimer !== undefined || this.catchupRetryAttempts >= 3) return;
    this.catchupRetryAttempts += 1;
    this.catchupRetryTimer = window.setTimeout(() => {
      this.catchupRetryTimer = undefined;
      void this.startCatchup().catch(() => this.scheduleCatchupRetry());
    }, 60_000);
  }
  stopCatchup() {
    this.runId++;
    this.stopped = true;
    this.catchupEnabled = false;
    this.catchupInterrupted = true;
    if (this.catchupIntervalTimer !== undefined) window.clearInterval(this.catchupIntervalTimer);
    if (this.catchupRetryTimer !== undefined) window.clearTimeout(this.catchupRetryTimer);
    if (this.catchupRestartTimer !== undefined) window.clearTimeout(this.catchupRestartTimer);
    this.catchupIntervalTimer = undefined;
    this.catchupRetryTimer = undefined;
    this.catchupRestartTimer = undefined;
    this.panel.hideCatchupStatus();
    this.panel.dispose();
  }
  start() {
    const stopPage = this.adapter.observePageChange(() => {
      if (this.initialized) void this.handlePageChange();
    });
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
      stopMessages();
      stopResume();
      if (this.catchupIntervalTimer !== undefined) {
        window.clearInterval(this.catchupIntervalTimer);
        this.catchupIntervalTimer = undefined;
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
        // A failed capture is never retried from the page. Automatic
        // re-capture used to fire minutes later while the recruiter was
        // working in the same conversation and hijack it again; the next
        // confirmed invitation is the natural retry point.
      }
    };
    this.snapshotInFlight = this.snapshotInFlight.then(task, task);
    await this.snapshotInFlight;
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
