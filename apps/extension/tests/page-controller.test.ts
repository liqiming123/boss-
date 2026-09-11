import { afterEach, describe, expect, it, vi } from "vitest";
import {
  bossRowMatchesCandidate,
  PageController,
} from "../src/content/page-controller";
import type {
  AdapterDiagnostics,
  Extraction,
  RecruiterMessageSent,
  RecruitmentSiteAdapter,
} from "../src/adapters/types";
import { classifyBossOutgoingMessage } from "../src/adapters/boss/boss-status";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const eventually = async (predicate: () => boolean) => {
  for (let attempt = 0; attempt < 20; attempt++) {
    if (predicate()) return;
    await tick();
  }
  expect(predicate()).toBe(true);
};

class CandidateSwitchAdapter implements RecruitmentSiteAdapter {
  readonly platform = "boss";
  candidate = "甲候选人";
  callback = () => {};
  messageCallback: (event: RecruiterMessageSent) => void = () => {};
  active = true;
  canHandle() {
    return true;
  }
  isCandidateConversationPage() {
    return this.active;
  }
  async extractAccount(): Promise<Extraction<{ displayName: string }>> {
    return { status: "OK", value: { displayName: "页面账号" } };
  }
  async extractCandidate(): Promise<Extraction<{ displayName: string }>> {
    return { status: "OK", value: { displayName: this.candidate } };
  }
  async extractJob(): Promise<Extraction<{ displayName: string }>> {
    return { status: "OK", value: { displayName: "AI应用开发工程师" } };
  }
  observePageChange(callback: () => void) {
    this.callback = callback;
    return () => {};
  }
  observeRecruiterMessageSent(callback: (event: RecruiterMessageSent) => void) {
    this.messageCallback = callback;
    return () => {};
  }
  async getDiagnostics(): Promise<AdapterDiagnostics> {
    return {
      platform: "boss",
      adapterVersion: "test-adapter",
      pageType: "/web/chat/index",
      accountStatus: "OK",
      candidateStatus: "OK",
      jobStatus: "OK",
      platformIdStatus: "MISSING",
      errorCodes: [],
      sanitizedContext: { test: true },
    };
  }
}

describe("Page controller candidate activation", () => {
  it("ignores late lookup replies and new page/message events after logout", async () => {
    let resolveLookup!: (value: unknown) => void;
    const sendMessage = vi.fn().mockImplementation((message) => {
      if (message.type === "CHECK_CONTEXT") return new Promise(resolve => { resolveLookup = resolve; });
      return Promise.resolve({ ok: true, data: { catchupEnabled: false, catchup_enabled: false } });
    });
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    const controller = new PageController(adapter);
    const dispose = controller.start();
    await eventually(() => !!resolveLookup);
    controller.stopCatchup();
    const count = sendMessage.mock.calls.length;
    resolveLookup({ ok: false, error: "late failure" });
    adapter.candidate = "退出后的候选人";
    adapter.callback();
    await tick();
    expect(sendMessage.mock.calls).toHaveLength(count);
    expect(document.querySelector<HTMLElement>("#recruitment-collab-host")!.style.display).toBe("none");
    dispose();
  });
  afterEach(() => {
    document.querySelector("#recruitment-collab-host")?.remove();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });
  it("checks the initially selected candidate and checks again only when selection changes", async () => {
    const sendMessage = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        candidate_source_id: "source",
        result_type: "NO_HISTORY",
        ui: { severity: "success", title: "无重复", message: "已自动扫描" },
        matches: [],
        available_actions: [],
        account_mapping: {},
        job_mapping: {},
      },
    });
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    new PageController(adapter).start();
    await tick();
    const resolveCalls = () =>
      sendMessage.mock.calls.filter(
        ([message]) => message.type === "CHECK_CONTEXT",
      );
    expect(resolveCalls()).toHaveLength(1);
    adapter.callback();
    await tick();
    expect(resolveCalls()).toHaveLength(1);
    adapter.candidate = "乙候选人";
    adapter.callback();
    await tick();
    expect(resolveCalls()).toHaveLength(2);
    adapter.callback();
    await tick();
    expect(resolveCalls()).toHaveLength(2);
  });
  it("drops a mid-switch snapshot instead of merging two candidates", async () => {
    const sendMessage = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        candidate_source_id: "source",
        result_type: "NO_HISTORY",
        ui: { severity: "success", title: "无重复", message: "" },
        matches: [],
        available_actions: [],
        account_mapping: {},
        job_mapping: {},
      },
    });
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    // The first read sees 甲候选人; by the confirmation read the recruiter has
    // switched and the pane shows 乙候选人. Neither snapshot may be persisted.
    let reads = 0;
    adapter.extractCandidate = async () => ({
      status: "OK",
      value: { displayName: reads++ === 0 ? "甲候选人" : "乙候选人" },
    });
    new PageController(adapter).start();
    await tick();
    await tick();
    const types = sendMessage.mock.calls.map(([message]) => message.type);
    expect(types).not.toContain("SYNC_CONVERSATION");
    expect(types).not.toContain("CHECK_CONTEXT");
  });
  it("does not start automatic catch-up when the device switch is disabled", async () => {
    const sendMessage = vi
      .fn()
      .mockImplementation(({ type }: { type: string }) =>
        type === "GET_AUTH"
          ? Promise.resolve({
              ok: true,
              data: {
                apiBaseUrl: "https://api.example.com/api/v1",
                catchupEnabled: false,
              },
            })
          : Promise.resolve({
              ok: true,
              data: {
                candidate_source_id: null,
                result_type: "CHECK_ONLY_NO_HISTORY",
                ui: { severity: "success", title: "", message: "" },
                matches: [],
                available_actions: [],
                account_mapping: {},
                job_mapping: {},
              },
            }),
      );
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    new PageController(new CandidateSwitchAdapter()).start();
    await tick();
    await tick();
    expect(
      sendMessage.mock.calls.some(
        ([message]) => message.type === "GET_SCAN_CHECKPOINT",
      ),
    ).toBe(false);
  });
  it("starts catch-up when the live company setting repairs a stale disabled cache", async () => {
    const sendMessage = vi
      .fn()
      .mockImplementation(({ type }: { type: string }) => {
        if (type === "GET_AUTH")
          return Promise.resolve({
            ok: true,
            data: {
              apiBaseUrl: "https://api.example.com/api/v1",
              catchupEnabled: false,
            },
          });
        if (type === "GET_PLUGIN_SETTINGS")
          return Promise.resolve({ ok: true, data: { catchup_enabled: true } });
        if (type === "GET_SCAN_CHECKPOINT")
          return Promise.resolve({
            ok: true,
            data: { completed_through_at: "2026-09-03T00:00:00.000Z" },
          });
        return Promise.resolve({
          ok: true,
          data: {
            candidate_source_id: null,
            result_type: "CHECK_ONLY_NO_HISTORY",
            ui: { severity: "success", title: "", message: "" },
            matches: [],
            available_actions: [],
            account_mapping: {},
            job_mapping: {},
          },
        });
      });
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    new PageController(new CandidateSwitchAdapter()).start();
    await tick();
    await tick();
    expect(
      sendMessage.mock.calls.some(
        ([message]) => message.type === "GET_SCAN_CHECKPOINT",
      ),
    ).toBe(true);
  });
  it("waits for the BOSS account shell before requesting the catch-up checkpoint", async () => {
    vi.useFakeTimers();
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) => {
      if (type === "GET_AUTH")
        return Promise.resolve({ ok: true, data: { catchupEnabled: true } });
      if (type === "GET_PLUGIN_SETTINGS")
        return Promise.resolve({ ok: true, data: { catchup_enabled: true } });
      if (type === "GET_SCAN_CHECKPOINT")
        return Promise.resolve({
          ok: true,
          data: { completed_through_at: "2026-09-03T00:00:00.000Z" },
        });
      return Promise.resolve({ ok: true, data: {} });
    });
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    let accountAttempts = 0;
    adapter.extractAccount = async () =>
      ++accountAttempts < 3
        ? { status: "ERROR", errorCode: "BOSS_FIELDS_NOT_FOUND" }
        : { status: "OK", value: { displayName: "页面账号" } };
    new PageController(adapter).start();
    await vi.advanceTimersByTimeAsync(1_100);
    expect(accountAttempts).toBeGreaterThanOrEqual(3);
    expect(
      sendMessage.mock.calls.some(
        ([message]) => message.type === "GET_SCAN_CHECKPOINT",
      ),
    ).toBe(true);
  });
  it("syncs every clicked candidate whether or not outbound evidence exists", async () => {
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) =>
      Promise.resolve({
        ok: true,
        data:
          type === "SYNC_CONVERSATION"
            ? { candidate_source_id: "source" }
            : type === "GET_AUTH"
              ? { catchupEnabled: false }
              : {
                  candidate_source_id: null,
                  result_type: "CHECK_ONLY_NO_HISTORY",
                  ui: { severity: "success", title: "", message: "" },
                  matches: [],
                  available_actions: [],
                  account_mapping: {},
                  job_mapping: {},
                },
      }),
    );
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    adapter.extractCandidate = async () => ({
      status: "OK",
      value: {
        displayName: adapter.candidate,
        conversationUpdatedAt: "2026-09-03T08:00:00.000Z",
        hasRecruiterOutbound: adapter.candidate === "已沟通候选人",
      },
    });
    new PageController(adapter).start();
    await eventually(
      () =>
        sendMessage.mock.calls.filter(
          ([message]) => message.type === "SYNC_CONVERSATION",
        ).length >= 1,
    );
    const initialSyncCalls = sendMessage.mock.calls.filter(
      ([message]) => message.type === "SYNC_CONVERSATION",
    );
    expect(initialSyncCalls).toHaveLength(1);
    expect(initialSyncCalls[0][0].payload).toMatchObject({
      has_recruiter_outbound: false,
      sync_reason: "CANDIDATE_OPENED",
    });
    adapter.candidate = "已沟通候选人";
    adapter.callback();
    await eventually(
      () =>
        sendMessage.mock.calls.filter(
          ([message]) => message.type === "SYNC_CONVERSATION",
        ).length >= 2,
    );
    const syncCalls = sendMessage.mock.calls.filter(
      ([message]) => message.type === "SYNC_CONVERSATION",
    );
    expect(syncCalls).toHaveLength(2);
    expect(syncCalls[1][0].payload).toMatchObject({
      has_recruiter_outbound: true,
      sync_reason: "CONVERSATION_UPDATED",
    });
  });
  it("shows only duplicate results and hides immediately after leaving the chat page", async () => {
    const sendMessage = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        candidate_source_id: "source",
        result_type: "SUSPECTED_DUPLICATE",
        ui: {
          severity: "warning",
          title: "发现候选人历史记录",
          message: "其他人正在跟进",
        },
        matches: [
          {
            recruiter_name: "其他招聘者",
            stage: "FOLLOWING",
            match_reason: "同名同岗位",
          },
        ],
        available_actions: [],
        account_mapping: {},
        job_mapping: {},
      },
    });
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    new PageController(adapter).start();
    await tick();
    adapter.candidate = "乙候选人";
    adapter.callback();
    await tick();
    const host = document.querySelector<HTMLElement>(
      "#recruitment-collab-host",
    )!;
    await eventually(() => host.style.display === "block");
    expect(host.style.display).toBe("block");
    adapter.active = false;
    adapter.callback();
    await tick();
    await tick();
    await eventually(() => host.style.display === "none");
    expect(host.style.display).toBe("none");
  });
  it("shows lifecycle status on the chat page when using the local development API", async () => {
    const sendMessage = vi
      .fn()
      .mockImplementation(({ type }: { type: string }) =>
        type === "GET_AUTH"
          ? Promise.resolve({
              ok: true,
              data: { apiBaseUrl: "http://127.0.0.1:8000/api/v1" },
            })
          : Promise.resolve({
              ok: true,
              data: {
                candidate_source_id: "source",
                result_type: "NO_HISTORY",
                ui: { severity: "success", title: "", message: "" },
                matches: [],
                available_actions: [],
                account_mapping: {},
                job_mapping: {},
              },
            }),
      );
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    new PageController(adapter).start();
    await eventually(() =>
      document
        .querySelector<HTMLElement>("#recruitment-collab-host")
        ?.shadowRoot?.textContent?.includes("候选人已同步；未发现其他同事记录") ??
      false,
    );
    const host = document.querySelector<HTMLElement>(
      "#recruitment-collab-host",
    )!;
    expect(host.shadowRoot?.textContent).toContain("候选人已同步；未发现其他同事记录");
    adapter.candidate = "乙候选人";
    adapter.callback();
    await eventually(() => host.shadowRoot?.textContent?.includes("候选人已同步；未发现其他同事记录") ?? false);
    adapter.messageCallback({
      sentAt: "2026-09-02T08:00:00.000Z",
      evidence: "DELIVERY_MARKER",
      messageText: "本轮不通过，暂不继续推进",
      statusEvidence: {
        status: "已拒绝",
        evidence: "EXPLICIT_REJECTION",
        ruleVersion: "boss-status-v4",
        observedAt: "2026-09-02T08:00:00.000Z",
      },
    });
    await tick();
    expect(
      sendMessage.mock.calls.filter(
        ([message]) => message.type === "MESSAGE_SENT",
      ),
    ).toHaveLength(1);
    expect(host.shadowRoot?.textContent).toContain("招聘消息已登记");
    adapter.active = false;
    adapter.callback();
    await tick();
    expect(host.style.display).toBe("none");
  });
  it("ends the development loading state when the background request times out", async () => {
    vi.useFakeTimers();
    const sendMessage = vi
      .fn()
      .mockImplementation(({ type }: { type: string }) =>
        type === "GET_AUTH"
          ? Promise.resolve({
              ok: true,
              data: { apiBaseUrl: "http://127.0.0.1:8000/api/v1" },
            })
          : new Promise(() => {}),
      );
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    new PageController(adapter, 10).start();
    await vi.runAllTimersAsync();
    adapter.candidate = "乙候选人";
    adapter.callback();
    await vi.advanceTimersByTimeAsync(11);
    const host = document.querySelector<HTMLElement>(
      "#recruitment-collab-host",
    )!;
    expect(host.style.display).toBe("block");
    expect(host.shadowRoot?.textContent).toContain("查重暂不可用");
    vi.useRealTimers();
  });
  it("still syncs the clicked candidate when duplicate lookup is unavailable", async () => {
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) =>
      type === "CHECK_CONTEXT"
        ? Promise.resolve({ ok: false, error: "FEISHU_LOOKUP_UNAVAILABLE" })
        : type === "GET_AUTH"
          ? Promise.resolve({ ok: true, data: { catchupEnabled: false } })
          : Promise.resolve({ ok: true, data: { candidate_source_id: "source" } }),
    );
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    new PageController(new CandidateSwitchAdapter()).start();
    await eventually(
      () =>
        sendMessage.mock.calls.filter(
          ([message]) => message.type === "SYNC_CONVERSATION",
        ).length > 0,
    );
    const syncCalls = sendMessage.mock.calls.filter(
        ([message]) => message.type === "SYNC_CONVERSATION",
      );
    expect(syncCalls.length).toBeGreaterThanOrEqual(1);
    expect(syncCalls.every(([message]) => message.payload.sync_reason === "CANDIDATE_OPENED")).toBe(true);
  });
  it("sends extracted chat timestamps and uses the confirmed sent time as latest activity", async () => {
    const sendMessage = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        candidate_source_id: "source",
        result_type: "NO_HISTORY",
        ui: { severity: "success", title: "", message: "" },
        matches: [],
        available_actions: [],
        account_mapping: {},
        job_mapping: {},
      },
    });
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    adapter.extractCandidate = async () => ({
      status: "OK",
      value: {
        displayName: adapter.candidate,
        conversationStartedAt: "2026-09-02T01:00:00.000Z",
        conversationUpdatedAt: "2026-09-02T02:00:00.000Z",
      },
    });
    new PageController(adapter).start();
    await tick();
    adapter.messageCallback({
      sentAt: "2026-09-02T08:00:00.000Z",
      evidence: "DELIVERY_MARKER",
      messageText: "本轮不通过，暂不继续推进",
      statusEvidence: {
        status: "已拒绝",
        evidence: "EXPLICIT_REJECTION",
        ruleVersion: "boss-status-v4",
        observedAt: "2026-09-02T08:00:00.000Z",
      },
    });
    await tick();
    const sent = sendMessage.mock.calls.find(
      ([message]) => message.type === "MESSAGE_SENT",
    )?.[0].payload;
    expect(sent.conversation_started_at).toBe("2026-09-02T01:00:00.000Z");
    expect(sent.conversation_updated_at).toBe("2026-09-02T08:00:00.000Z");
    expect(sent.recruitment_status).toBe("已拒绝");
    expect(sent.status_evidence).toBe("EXPLICIT_REJECTION");
    expect(sent.status_rule_version).toBe("boss-status-v4");
    // The same already-synced candidate is contacted again several days
    // later. Neither the previous fingerprint nor scan checkpoint may
    // suppress the new outbound event or replace its current intent.
    for (const [sentAt, messageText] of [
      ["2026-09-07T08:00:00.000Z", "您好，想继续和您聊聊岗位"],
      ["2026-09-07T08:01:00.000Z", "方便明天面试吗"],
    ]) {
      adapter.messageCallback({
        sentAt, messageText, evidence: "DELIVERY_MARKER",
        statusEvidence: classifyBossOutgoingMessage(messageText, sentAt),
      });
      await tick();
    }
    const sends = sendMessage.mock.calls.filter(([message]) => message.type === "MESSAGE_SENT")
      .map(([message]) => message.payload);
    expect(sends).toHaveLength(3);
    expect(sends.slice(1).map((payload) => payload.recruitment_status)).toEqual(["沟通中", "待约面"]);
    expect(sends[2].conversation_updated_at).toBe("2026-09-07T08:01:00.000Z");
    expect(sends.every((payload) => payload.account_display_name === "页面账号")).toBe(true);
    expect(new Set(sends.map((payload) => payload.client_event_id)).size).toBe(3);
    expect(sends.every((payload) => !("messageText" in payload))).toBe(true);
  });
  it("reports a sanitized diagnostic when an invitation snapshot cannot be captured", async () => {
    const sendMessage = vi
      .fn()
      .mockImplementation(({ type }: { type: string }) =>
        type === "GET_AUTH"
          ? Promise.resolve({
              ok: true,
              data: { apiBaseUrl: "http://127.0.0.1:8000/api/v1" },
            })
          : Promise.resolve({
              ok: true,
              data: {
                candidate_source_id: "source",
                result_type: "NO_HISTORY",
                ui: { severity: "success", title: "", message: "" },
                matches: [],
                available_actions: [],
                account_mapping: {},
                job_mapping: {},
              },
            }),
      );
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    new PageController(adapter).start();
    await tick();
    adapter.messageCallback({
      sentAt: "2026-09-02T08:00:00.000Z",
      evidence: "DELIVERY_MARKER",
      messageText: "面试邀请已发送",
      statusEvidence: classifyBossOutgoingMessage("面试邀请已发送"),
    });
    await tick();
    await tick();
    const diagnostic = sendMessage.mock.calls.find(
      ([message]) => message.type === "SEND_DIAGNOSTIC",
    )?.[0].payload;
    expect(diagnostic.error_codes).toContain("SNAPSHOT_CHAT_REGION_NOT_FOUND");
    expect(diagnostic.sanitized_context).toEqual(
      expect.objectContaining({ snapshotCapture: true }),
    );
  });
  it("never starts a chat capture for ordinary sends or for opening a candidate", async () => {
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) =>
      Promise.resolve({
        ok: true,
        data:
          type === "SYNC_CONVERSATION"
            ? { candidate_source_id: "source", snapshot_needed: true }
            : type === "GET_AUTH"
              ? { catchupEnabled: false }
              : type === "MESSAGE_SENT"
                ? {
                    candidate_source_id: "source",
                    result_type: "NO_HISTORY",
                    ui: { severity: "success", title: "", message: "" },
                    matches: [],
                    available_actions: [],
                    account_mapping: {},
                    job_mapping: {},
                  }
                : {
                    candidate_source_id: "source",
                    result_type: "NO_HISTORY",
                    ui: { severity: "success", title: "", message: "" },
                    matches: [],
                    available_actions: [],
                    account_mapping: {},
                    job_mapping: {},
                  },
      }),
    );
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    new PageController(adapter).start();
    await eventually(
      () =>
        sendMessage.mock.calls.filter(
          ([message]) => message.type === "SYNC_CONVERSATION",
        ).length >= 1,
    );
    adapter.messageCallback({
      sentAt: "2026-09-02T08:00:00.000Z",
      messageText: "您好，想和您聊聊岗位",
      evidence: "DELIVERY_MARKER",
      statusEvidence: classifyBossOutgoingMessage("您好，想和您聊聊岗位"),
    });
    await eventually(
      () =>
        sendMessage.mock.calls.filter(
          ([message]) => message.type === "MESSAGE_SENT",
        ).length >= 1,
    );
    await tick();
    await tick();
    // The server still reports snapshot_needed=true for the passive sync
    // above; a stale server flag must not resurrect the click-time capture.
    expect(
      sendMessage.mock.calls.some(
        ([message]) => message.type === "REPORT_SNAPSHOT_STATUS",
      ),
    ).toBe(false);
    expect(
      sendMessage.mock.calls.some(
        ([message]) => message.type === "UPLOAD_SNAPSHOT",
      ),
    ).toBe(false);
  });
  it("passes BOSS native colleague history into the read-only duplicate check", async () => {
    const sendMessage = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        candidate_source_id: null,
        result_type: "CONFIRMED_DUPLICATE",
        ui: {
          severity: "danger",
          title: "发现候选人历史记录",
          message: "王文懋 已沟通过",
        },
        matches: [
          {
            match_level: "CONFIRMED_BOSS_HISTORY",
            candidate_source_id: "boss-native:x",
            recruiter_id: "boss-native:王文懋",
            recruiter_name: "王文懋",
            job_id: null,
            job_name: "总经理助理",
            stage: "飞书未同步",
            updated_at: "2026-08-05T10:27:00Z",
            match_reason: "BOSS 原生记录",
            evidence_source: "BOSS_NATIVE",
            feishu_synced: false,
          },
        ],
        available_actions: [],
        account_mapping: {},
        job_mapping: {},
      },
    });
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    adapter.extractCandidate = async () => ({
      status: "OK",
      value: {
        displayName: adapter.candidate,
        age: 29,
        experience: "6年",
        education: "硕士",
        nativeCommunications: [
          {
            recruiterName: "王文懋",
            jobName: "总经理助理",
            contactedAt: "2026-08-05T18:27:00+08:00",
            source: "BOSS_NATIVE",
          },
        ],
      },
    });
    new PageController(adapter).start();
    await tick();
    await tick();
    const check = sendMessage.mock.calls.find(
      ([message]) => message.type === "CHECK_CONTEXT",
    )?.[0].payload;
    expect(check.native_communications).toEqual([
      {
        recruiter_name: "王文懋",
        job_name: "总经理助理",
        contacted_at: "2026-08-05T18:27:00+08:00",
        source: "BOSS_NATIVE",
      },
    ]);
    await eventually(() =>
      document.querySelector<HTMLElement>("#recruitment-collab-host")
        ?.shadowRoot?.textContent?.includes("BOSS已确认") ?? false,
    );
  });
});

describe("BOSS catch-up row identity", () => {
  it("accepts the detail only when both candidate and job belong to the clicked row", () => {
    const row = "昨天 顾嘉 雯 ai应用开发工程师 [已读]";
    expect(bossRowMatchesCandidate(row, "顾嘉雯", "ai应用开发工程师")).toBe(true);
    expect(bossRowMatchesCandidate(row, "朱晓滢", "ai应用开发工程师")).toBe(false);
    expect(bossRowMatchesCandidate(row, "顾嘉雯", "业务助理")).toBe(false);
  });
});
