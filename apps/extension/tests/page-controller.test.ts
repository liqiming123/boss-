import { afterEach, describe, expect, it, vi } from "vitest";
import { PageController } from "../src/content/page-controller";
import type {
  AdapterDiagnostics,
  Extraction,
  RecruiterMessageSent,
  RecruitmentSiteAdapter,
} from "../src/adapters/types";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

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
  afterEach(() => {
    document.querySelector("#recruitment-collab-host")?.remove();
    vi.unstubAllGlobals();
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
  it("syncs a clicked candidate only when current-chat outbound evidence exists", async () => {
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
    await tick();
    expect(
      sendMessage.mock.calls.filter(([message]) => message.type === "SYNC_CONVERSATION"),
    ).toHaveLength(0);
    adapter.candidate = "已沟通候选人";
    adapter.callback();
    await tick();
    await tick();
    const syncCalls = sendMessage.mock.calls.filter(
      ([message]) => message.type === "SYNC_CONVERSATION",
    );
    expect(syncCalls).toHaveLength(1);
    expect(syncCalls[0][0].payload).toMatchObject({
      has_recruiter_outbound: true,
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
    expect(host.style.display).toBe("block");
    adapter.active = false;
    adapter.callback();
    await tick();
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
    await tick();
    const host = document.querySelector<HTMLElement>(
      "#recruitment-collab-host",
    )!;
    expect(host.shadowRoot?.textContent).toContain("尚未发送消息，不创建记录");
    adapter.candidate = "乙候选人";
    adapter.callback();
    await tick();
    expect(host.shadowRoot?.textContent).toContain("尚未发送消息，不创建记录");
    adapter.messageCallback({
      sentAt: "2026-09-02T08:00:00.000Z",
      evidence: "DELIVERY_MARKER",
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
    expect(host.style.display).toBe("none");
    vi.useRealTimers();
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
    });
    await tick();
    const sent = sendMessage.mock.calls.find(
      ([message]) => message.type === "MESSAGE_SENT",
    )?.[0].payload;
    expect(sent.conversation_started_at).toBe("2026-09-02T01:00:00.000Z");
    expect(sent.conversation_updated_at).toBe("2026-09-02T08:00:00.000Z");
  });
  it("reports a sanitized diagnostic when the chat snapshot cannot be captured", async () => {
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
    expect(
      document.querySelector<HTMLElement>("#recruitment-collab-host")
        ?.shadowRoot?.textContent,
    ).toContain("BOSS已确认");
  });
});
