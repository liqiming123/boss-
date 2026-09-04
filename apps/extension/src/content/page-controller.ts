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
import { runBossCatchup } from "../adapters/boss/boss-catchup";
import { sha256Hex } from "../shared/sha256";
import { sendRuntimeMessage } from "../shared/runtime-message";

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
    this.panel.hide();
  }

  private async handlePageChange(force = false): Promise<boolean> {
    if (!this.adapter.isCandidateConversationPage()) {
      this.leaveCandidatePage();
      return false;
    }
    const fields = await this.fields();
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
    this.lastFingerprint = fields.fingerprint;
    const id = ++this.runId;
    if (this.development)
      this.panel.showDevelopmentStatus("正在检查其他招聘人员的跟进记录…");
    const response = await this.send("CHECK_CONTEXT", this.payload(fields));
    if (id !== this.runId || !this.adapter.isCandidateConversationPage())
      return false;
    // Collection, synchronization and lookup failures are intentionally
    // silent in the page. The in-page surface is reserved exclusively for
    // actionable duplicate evidence; binding/network details remain in the
    // extension popup and diagnostics.
    if (!response.ok) {
      this.panel.hide();
      return false;
    }
    const data = response.data as ContextResponse;
    if (data.result_type === "JOB_UNMAPPED") {
      if (this.development)
        this.panel.showDevelopmentStatus(`未检查：${data.ui.title}`);
      else this.panel.hide();
      return false;
    }
    const synchronized = await this.syncObserved(fields);
    this.showResult(
      data,
      fields.hasRecruiterOutbound
        ? "招聘消息已登记；未发现其他同事跟进"
        : "检查完成；尚未发送消息，不创建记录",
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
    if (
      !fields.hasRecruiterOutbound ||
      !fields.accountDisplayName ||
      !fields.conversationUpdatedAt
    )
      return true;
    const jobs = [
      ...new Set([fields.jobDisplayName, ...fields.historicalJobs]),
    ].filter(Boolean);
    const key = `${fields.fingerprint}\u0000${jobs.slice().sort().join("|")}\u0000${fields.conversationUpdatedAt}\u0000${fields.recruitmentStatus}`;
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
    const sources: string[] = [];
    for (const job of jobs) {
      const result = await this.syncObservedJob(fields, job, key);
      if (!result) {
        this.lastObservedSync = "";
        return false;
      }
      if (result.source) sources.push(result.source);
    }
    if (!sources.length) return true;
    const current = await this.fields();
    if (current?.fingerprint === fields.fingerprint) {
      await this.uploadSnapshot(sources);
    }
    return true;
  }

  private async syncObservedJob(
    fields: ResolvedFields,
    job: string,
    key: string,
  ) {
    const response = await this.send("SYNC_CONVERSATION", {
      ...this.payload(fields, job),
      client_event_id: `scan-${await this.digest(`${key}\u0000${job}`)}`,
      sent_at: fields.conversationUpdatedAt,
      has_recruiter_outbound: true,
      sync_reason: "CONVERSATION_UPDATED",
    });
    if (!response.ok) return null;
    const data = response.data as { candidate_source_id?: string } | undefined;
    return { source: data?.candidate_source_id };
  }

  private async handleRecruiterMessageSent(event: RecruiterMessageSent) {
    if (!this.initialized || !this.adapter.isCandidateConversationPage())
      return;
    const fields = await this.fields();
    if (!fields || !this.adapter.isCandidateConversationPage()) return;
    this.lastFingerprint = fields.fingerprint;
    const id = ++this.runId;
    if (this.development)
      this.panel.showDevelopmentStatus("招聘消息已发送，正在登记跟进记录…");
    const extracted = this.payload(fields);
    const response = await this.send("MESSAGE_SENT", {
      ...extracted,
      conversation_updated_at: this.latestTime(
        extracted.conversation_updated_at,
        event.sentAt,
      ),
      sent_at: event.sentAt,
      recruitment_status: fields.recruitmentStatus,
      status_evidence: fields.statusEvidence,
      status_rule_version: fields.statusRuleVersion,
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
    void this.uploadSnapshot([data.candidate_source_id]);
  }

  private async initialize() {
    const auth = await this.send("GET_AUTH", {});
    const state = auth.ok
      ? (auth.data as
          { apiBaseUrl?: string; catchupEnabled?: boolean } | undefined)
      : undefined;
    const base = state?.apiBaseUrl || "";
    this.catchupEnabled = state?.catchupEnabled !== false;
    this.development =
      /^http:\/\/(localhost|127\.0\.0\.1)(?::\d+)?(?:\/|$)/.test(base);
    this.initialized = true;
    await this.handlePageChange();
    // Settings are operationally optional. Do not hold the first duplicate
    // check (or message observer) hostage to a slow/unavailable settings
    // endpoint; apply the server value when it arrives and only then decide
    // whether the background catch-up pass should start.
    void this.send("GET_PLUGIN_SETTINGS", {}).then((remote) => {
      if (remote?.ok) {
        this.catchupEnabled = (remote.data as { catchup_enabled: boolean }).catchup_enabled;
      }
    });
    void this.startCatchup();
  }
  private async startCatchup() {
    if (this.adapter.platform !== "boss" || !this.catchupEnabled) return;
    const fields = await this.fields();
    if (!fields?.accountDisplayName) return;
    const response = await this.send("GET_SCAN_CHECKPOINT", {
      platform: "boss",
      account_display_name: fields.accountDisplayName,
    });
    if (!response.ok) return;
    const watermark = (response.data as { completed_through_at: string })
      .completed_through_at;
    const scanStartedAt = new Date().toISOString();
    const result = await runBossCatchup(
      watermark,
      fields.candidateDisplayName,
      async () => this.handlePageChange(true),
    );
    // A checkpoint represents a fully completed traversal. A partial scan
    // deliberately keeps the old watermark so skipped rows are retried.
    if (result.available && result.complete) {
      await this.send("PUT_SCAN_CHECKPOINT", {
        platform: "boss",
        account_display_name: fields.accountDisplayName,
        completed_through_at: scanStartedAt,
        cursor: { scanned: result.scanned, complete: true },
      });
    }
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
    };
  }
  private async handleResumePreview(event: {
    url?: string;
    fileName?: string;
    target?: HTMLElement;
  }) {
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
    retryCapture = true,
  ) {
    const sources = candidateSourceIds.filter(
      (value): value is string => !!value,
    );
    if (!sources.length || this.adapter.platform !== "boss") return;
    const task = async () => {
      try {
        const snapshot = await captureBossConversationSnapshot();
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
        // Upload failures already retain their bounded image payload in the
        // background queue. A capture failure has no payload, so retry once
        // while the same BOSS tab remains open and idle.
        if (retryCapture && this.adapter.isCandidateConversationPage())
          window.setTimeout(
            () => void this.uploadSnapshot(sources, false),
            code === "SNAPSHOT_INTERRUPTED_BY_USER" ? 30_000 : 60_000,
          );
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
