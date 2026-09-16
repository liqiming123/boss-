import { afterEach, describe, expect, it, vi } from "vitest";
import {
  bossRowMatchesCandidate,
  dateKey,
  dateKeyString,
  historyCatchupDueAt,
  isSuspiciousEmptyPass,
  nextCatchupWindowDelayMs,
  nextSweepAnchor,
  pageOpenSweepDue,
  PageController,
  sweepAnchorDay,
} from "../src/content/page-controller";
import type {
  AdapterDiagnostics,
  Extraction,
  RecruiterMessageSent,
  RecruitmentSiteAdapter,
} from "../src/adapters/types";
import { classifyBossOutgoingMessage } from "../src/adapters/boss/boss-status";
import { isWithinSweepScope } from "../src/adapters/boss/boss-catchup";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const eventually = async (predicate: () => boolean) => {
  for (let attempt = 0; attempt < 20; attempt++) {
    if (predicate()) return;
    await tick();
  }
  expect(predicate()).toBe(true);
};

/** Wait until a call count stops growing, so an already-dispatched request
 * cannot land after the test clears the mock and pollute its assertions. */
const channelSettled = async (count: () => number, turns = 6) => {
  let stable = 0;
  let last = count();
  for (let attempt = 0; attempt < 30 && stable < turns; attempt++) {
    await tick();
    const current = count();
    stable = current === last ? stable + 1 : 0;
    last = current;
  }
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
  async extractCandidate(): Promise<Extraction<{ displayName: string; hasRecruiterOutbound?: boolean }>> {
    return { status: "OK", value: { displayName: this.candidate, hasRecruiterOutbound: true } };
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
        ([message]) => message.type === "GET_CONVERSATION_INDEX",
      ),
    ).toBe(false);
  });
  it("leaves a page open alone once today's 23:30 window has been swept", async () => {
    vi.useFakeTimers();
    const key = "boss-catchup-history:页面账号";
    const store: Record<string, unknown> = {
      [key]: { last_completed_at: new Date().toISOString() },
    };
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) => {
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
      return Promise.resolve({ ok: true, data: {} });
    });
    vi.stubGlobal("chrome", {
      runtime: { sendMessage },
      storage: {
        local: {
          get: (name: string, callback: (value: Record<string, unknown>) => void) =>
            callback(store[name] === undefined ? {} : { [name]: store[name] }),
          set: (value: Record<string, unknown>, callback?: () => void) => {
            Object.assign(store, value);
            callback?.();
          },
        },
      },
    });
    const controller = new PageController(new CandidateSwitchAdapter());
    controller.start();
    // The live switch repairs the stale local cache, but today's window is
    // already covered, so opening the page must not traverse — not even after
    // the quiet period a missed window would have to wait out. The light
    // per-open reconcile was removed: only a missed window traverses.
    await vi.advanceTimersByTimeAsync(120_000);
    expect(
      sendMessage.mock.calls.some(
        ([message]) => message.type === "GET_CONVERSATION_INDEX",
      ),
    ).toBe(false);
    controller.stopCatchup();
    vi.useRealTimers();
  });
  it("waits for the BOSS account shell before sweeping a missed window", async () => {
    vi.useFakeTimers();
    const key = "boss-catchup-history:页面账号";
    const store: Record<string, unknown> = {
      // Yesterday's pass does not cover the window that has since passed.
      [key]: { last_completed_at: "2026-09-14T21:05:00+08:00" },
    };
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) => {
      if (type === "GET_AUTH")
        return Promise.resolve({ ok: true, data: { catchupEnabled: true } });
      if (type === "GET_PLUGIN_SETTINGS")
        return Promise.resolve({ ok: true, data: { catchup_enabled: true } });
      if (type === "GET_CONVERSATION_INDEX")
        return Promise.resolve({
          ok: true,
          data: { completed_through_at: "2026-09-03T00:00:00.000Z" },
        });
      return Promise.resolve({ ok: true, data: {} });
    });
    vi.stubGlobal("chrome", {
      runtime: { sendMessage },
      storage: {
        local: {
          get: (name: string, callback: (value: Record<string, unknown>) => void) =>
            callback(store[name] === undefined ? {} : { [name]: store[name] }),
          set: (value: Record<string, unknown>, callback?: () => void) => {
            Object.assign(store, value);
            callback?.();
          },
        },
      },
    });
    const adapter = new CandidateSwitchAdapter();
    let accountAttempts = 0;
    adapter.extractAccount = async () =>
      ++accountAttempts < 3
        ? { status: "ERROR", errorCode: "BOSS_FIELDS_NOT_FOUND" }
        : { status: "OK", value: { displayName: "页面账号" } };
    const controller = new PageController(adapter);
    controller.start();
    // The shell is painted in stages on a reload, so the make-up sweep keeps
    // re-reading the account instead of giving up on the first miss.
    await vi.advanceTimersByTimeAsync(90_000);
    expect(accountAttempts).toBeGreaterThanOrEqual(3);
    expect(
      sendMessage.mock.calls.some(
        ([message]) => message.type === "GET_CONVERSATION_INDEX",
      ),
    ).toBe(true);
    controller.stopCatchup();
    vi.useRealTimers();
  });
  it("does not turn historical outbound evidence into a new conversation event", async () => {
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
    // The mount observation dispatches SYNC_CONVERSATION in the same turn as
    // CHECK_CONTEXT, but the request is only recorded once its async id is
    // resolved. Wait for it instead of assuming it already landed.
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
      sync_reason: "CANDIDATE_OPENED",
    });
  });
  it("checks Feishu history during catch-up even without recruiter outbound", async () => {
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) => Promise.resolve({
      ok: true,
      data: type === "SYNC_CONVERSATION" ? { candidate_source_id: "source" } : {
        candidate_source_id: null,
        result_type: "CONFIRMED_DUPLICATE",
        ui: { severity: "danger", title: "发现候选人历史记录", message: "其他招聘者已沟通过" },
        matches: [{ recruiter_name: "其他招聘者" }],
        available_actions: [],
        account_mapping: {},
        job_mapping: {},
      },
    }));
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    adapter.extractCandidate = async () => ({
      status: "OK",
      value: { displayName: adapter.candidate, hasRecruiterOutbound: false },
    });
    const controller = new PageController(adapter);
    controller.start();
    await eventually(() => sendMessage.mock.calls.some(([message]) => message.type === "CHECK_CONTEXT"));
    // Let the mount-time observation finish before clearing. Its sync request
    // is dispatched in the same turn as CHECK_CONTEXT, so clearing too early
    // let that earlier call land inside the assertion window.
    await channelSettled(() =>
      sendMessage.mock.calls.filter(([message]) => message.type === "SYNC_CONVERSATION").length,
    );
    sendMessage.mockClear();
    // shouldOpen runs first in production and is what selects the sync reason.
    (controller as unknown as { pendingCatchupSyncReason: string }).pendingCatchupSyncReason =
      "HISTORY_SNAPSHOT";
    const completed = await (controller as unknown as {
      handleCatchupCandidate(rowText: string, listActivityAt: string): Promise<boolean>;
    }).handleCatchupCandidate("甲候选人 AI应用开发工程师 09:35", "2026-09-14T09:35:00.000Z");
    expect(completed).toBe(true);
    expect(sendMessage.mock.calls.some(([message]) => message.type === "CHECK_CONTEXT")).toBe(true);
    // Reconciliation now owns the Feishu write: a row opened because its BOSS
    // list time was newer must sync even when this recruiter sent nothing.
    const syncs = sendMessage.mock.calls.filter(([message]) => message.type === "SYNC_CONVERSATION");
    expect(syncs).toHaveLength(1);
    expect(syncs[0][0].payload).toMatchObject({
      sync_reason: "HISTORY_SNAPSHOT",
      sent_at: "2026-09-14T09:35:00.000Z",
    });
    // The mock page has no chat region, so a capture attempt ends in the
    // sanitized status path. This proves catch-up snapshots do not need an
    // interview status or recruiter outbound evidence.
    expect(sendMessage.mock.calls.some(
      ([message]) => message.type === "REPORT_SNAPSHOT_STATUS",
    )).toBe(true);
  });
  it("names the real reason a duplicate lookup was unavailable", async () => {
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) =>
      type === "GET_AUTH"
        ? Promise.resolve({ ok: true, data: { catchupEnabled: false } })
        : Promise.resolve({ ok: false, error: "INVALID_TOKEN" }),
    );
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    const controller = new PageController(adapter);
    controller.start();
    await eventually(
      () => document.querySelector("#recruitment-collab-host")!.shadowRoot!.textContent!.includes("查重暂不可用"),
    );
    const panelText = document.querySelector("#recruitment-collab-host")!.shadowRoot!.textContent!;
    // An expired login must not be reported as a Feishu read failure.
    expect(panelText).toContain("登录已失效");
    expect(panelText).not.toContain("无法读取飞书记录");
  });
  it("schedules one automatic sweep per day at 23:30 Shanghai time", () => {
    const at = (value: string) => Date.parse(value);
    // Working hours: the window is later the same evening.
    expect(nextCatchupWindowDelayMs(at("2026-09-15T08:00:00+08:00"))).toBe(15.5 * 60 * 60 * 1000);
    expect(nextCatchupWindowDelayMs(at("2026-09-15T22:00:00+08:00"))).toBe(1.5 * 60 * 60 * 1000);
    // Past the window (including the 23:30 → 24:00 stretch and after midnight)
    // the next one is the following evening.
    expect(nextCatchupWindowDelayMs(at("2026-09-15T23:31:00+08:00"))).toBe(23 * 60 * 60 * 1000 + 59 * 60 * 1000);
    expect(nextCatchupWindowDelayMs(at("2026-09-15T23:59:00+08:00"))).toBe(23.5 * 60 * 60 * 1000 + 60 * 1000);
    expect(nextCatchupWindowDelayMs(at("2026-09-16T00:30:00+08:00"))).toBe(23 * 60 * 60 * 1000);
    expect(nextCatchupWindowDelayMs(at("2026-09-16T09:00:00+08:00"))).toBe(14.5 * 60 * 60 * 1000);
  });
  it("owes a sweep when a window passed without a completed pass", () => {
    const at = (value: string) => Date.parse(value);
    const now = at("2026-09-15T14:30:00+08:00");
    // Never swept on this machine: the window before 14:30 is still owed.
    expect(historyCatchupDueAt({}, now)).toBe(true);
    // Last night's sweep covers the window that closed at 23:30.
    expect(historyCatchupDueAt({ last_completed_at: "2026-09-14T23:35:00+08:00" }, now)).toBe(false);
    // A pass from yesterday afternoon is behind that window.
    expect(historyCatchupDueAt({ last_completed_at: "2026-09-14T22:00:00+08:00" }, now)).toBe(true);
    // A sweep that already ran this morning satisfies the same window.
    expect(historyCatchupDueAt({ last_completed_at: "2026-09-15T09:05:00+08:00" }, now)).toBe(false);
    // A sweep that started ten minutes ago is not repeated on every reload.
    expect(historyCatchupDueAt({ last_attempt_at: "2026-09-15T14:20:00+08:00" }, now)).toBe(false);
    // ...but an old failed/never-finished attempt is retried.
    expect(historyCatchupDueAt({ last_attempt_at: "2026-09-15T10:00:00+08:00" }, now)).toBe(true);
    // Tonight's window owes another pass.
    expect(
      historyCatchupDueAt(
        { last_completed_at: "2026-09-15T14:10:00+08:00" },
        at("2026-09-15T23:40:00+08:00"),
      ),
    ).toBe(true);
  });
  it("owes a full sweep when today's window was missed while the browser was closed", async () => {
    const key = "boss-catchup-history:页面账号";
    const store: Record<string, unknown> = {
      [key]: { last_completed_at: "2026-09-14T21:05:00+08:00" },
    };
    const local = {
      get: (name: string, callback: (value: Record<string, unknown>) => void) =>
        callback({ [name]: store[name] }),
      set: (value: Record<string, unknown>, callback?: () => void) => {
        Object.assign(store, value);
        callback?.();
      },
    };
    vi.stubGlobal("chrome", { runtime: { sendMessage: vi.fn() }, storage: { local } });
    // Yesterday's sweep does not cover the window that has since passed.
    expect(pageOpenSweepDue(store[key] as never, true)).toBe(true);
    // Once the newest window has a completed pass, an open page stays cheap.
    store[key] = { last_completed_at: new Date().toISOString() };
    expect(pageOpenSweepDue(store[key] as never, true)).toBe(false);
    // An attempt that is still recent is not repeated on every reload.
    store[key] = { last_attempt_at: new Date().toISOString() };
    expect(pageOpenSweepDue(store[key] as never, true)).toBe(false);
    // Without a watermark store the lightweight pass is kept.
    expect(pageOpenSweepDue({}, false)).toBe(false);
  });
  it("waits for a quiet page before running the missed-window sweep", async () => {
    vi.useFakeTimers();
    const store: Record<string, unknown> = {};
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) =>
      type === "GET_AUTH"
        ? Promise.resolve({ ok: true, data: { catchupEnabled: true } })
        : Promise.resolve({ ok: true, data: {} }),
    );
    vi.stubGlobal("chrome", {
      runtime: { sendMessage },
      storage: {
        local: {
          get: (key: string, callback: (value: Record<string, unknown>) => void) =>
            callback(store[key] === undefined ? {} : { [key]: store[key] }),
          set: (value: Record<string, unknown>, callback?: () => void) => {
            Object.assign(store, value);
            callback?.();
          },
        },
      },
    });
    const controller = new PageController(new CandidateSwitchAdapter());
    const internals = controller as unknown as {
      startCatchup: (historySnapshot?: boolean) => Promise<void>;
    };
    // No watermark at all: the day is owed, but the page was just opened, so
    // nothing may be traversed under the recruiter's hands.
    await internals.startCatchup();
    expect(sendMessage.mock.calls.some(([message]) => message.type === "GET_CONVERSATION_INDEX")).toBe(false);
    // Once the page has been quiet for a minute the deferred sweep starts.
    await vi.advanceTimersByTimeAsync(70_000);
    expect(sendMessage.mock.calls.some(([message]) => message.type === "GET_CONVERSATION_INDEX")).toBe(true);
    // A manual "立即检查" is user-initiated and still starts immediately.
    sendMessage.mockClear();
    await internals.startCatchup(true);
    expect(sendMessage.mock.calls.some(([message]) => message.type === "GET_CONVERSATION_INDEX")).toBe(true);
    controller.stopCatchup();
    vi.useRealTimers();
  });
  it("treats a completed pass that saw no conversation row as a failure", () => {
    // A list that never rendered must retry instead of recording a sweep.
    expect(isSuspiciousEmptyPass({ available: true, complete: true }, 0)).toBe(true);
    // A real pass that priced rows without opening one is still a success.
    expect(isSuspiciousEmptyPass({ available: true, complete: true }, 16)).toBe(false);
    // An interrupted pass is handled by the existing retry path.
    expect(isSuspiciousEmptyPass({ available: false, complete: false }, 0)).toBe(false);
    expect(isSuspiciousEmptyPass({ available: true, complete: false }, 0)).toBe(false);
  });
  it("captures the first image of a row the server reports as having none", async () => {
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) => {
      if (type === "GET_AUTH")
        return Promise.resolve({ ok: true, data: { catchupEnabled: false } });
      if (type === "SYNC_CONVERSATION")
        return Promise.resolve({
          ok: true,
          data: { candidate_source_id: "source", snapshot_needed: true },
        });
      return Promise.resolve({
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
    });
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    new PageController(adapter).start();
    await eventually(
      () =>
        sendMessage.mock.calls.filter(
          ([message]) => message.type === "SYNC_CONVERSATION",
        ).length >= 1,
    );
    // jsdom has no BOSS chat region, so the attempt surfaces as the sanitized
    // snapshot-status report instead of a real upload.
    await eventually(() =>
      sendMessage.mock.calls.some(
        ([message]) => message.type === "REPORT_SNAPSHOT_STATUS",
      ),
    );
  });
  it("does not start a first-image capture while the recruiter is typing", async () => {
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) => {
      if (type === "GET_AUTH")
        return Promise.resolve({ ok: true, data: { catchupEnabled: false } });
      if (type === "SYNC_CONVERSATION")
        return Promise.resolve({
          ok: true,
          data: { candidate_source_id: "source", snapshot_needed: true },
        });
      return Promise.resolve({ ok: true, data: { candidate_source_id: "source" } });
    });
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    document.body.innerHTML = "<textarea id='composer'></textarea>";
    document.querySelector<HTMLTextAreaElement>("#composer")!.focus();
    const controller = new PageController(new CandidateSwitchAdapter());
    controller.start();
    await eventually(
      () =>
        sendMessage.mock.calls.filter(
          ([message]) => message.type === "SYNC_CONVERSATION",
        ).length >= 1,
    );
    await tick();
    await tick();
    expect(
      sendMessage.mock.calls.some(
        ([message]) =>
          message.type === "REPORT_SNAPSHOT_STATUS" ||
          message.type === "UPLOAD_SNAPSHOT",
      ),
    ).toBe(false);
    document.body.innerHTML = "";
  });
  it("anchors a sweep on the last calendar day it covered", () => {
    const at = (value: string) => Date.parse(value);
    const now = at("2026-09-15T14:30:00+08:00");
    // Nothing covered yet: the sweep starts at yesterday, so today is the scope
    // and a fresh install never walks the whole list.
    expect(dateKeyString(sweepAnchorDay({}, now))).toBe("2026-09-14");
    expect(dateKeyString(sweepAnchorDay({ swept_through_at: "not-a-time" }, now))).toBe("2026-09-14");
    // A pass that ran yesterday left the anchor on the day before it, so the
    // scope still contains the day that just ended — the day a browser closed at
    // 23:30 would otherwise never have swept.
    expect(dateKeyString(sweepAnchorDay({ swept_through_date: "2026-09-13" }, now))).toBe("2026-09-13");
    // A browser that was away for longer makes the next visit cover every
    // missed day as well.
    expect(dateKeyString(sweepAnchorDay({ swept_through_date: "2026-09-12" }, now))).toBe("2026-09-12");
    // An anchor from today or later cannot widen the scope past today.
    expect(dateKeyString(sweepAnchorDay({ swept_through_date: "2026-09-15" }, now))).toBe("2026-09-14");
    expect(dateKeyString(sweepAnchorDay({ swept_through_date: "2027-01-01" }, now))).toBe("2026-09-14");
    // Installs that still carry only the older timestamp anchor, or only a
    // completion time, keep the day those values fell on.
    expect(dateKeyString(sweepAnchorDay({ swept_through_at: "2026-09-13T22:10:00+08:00" }, now))).toBe("2026-09-13");
    expect(dateKeyString(sweepAnchorDay({ last_completed_at: "2026-09-13T23:35:00+08:00" }, now))).toBe("2026-09-13");

    // A finished sweep records the day that has ended, not the one it ran in:
    // a sweep can only ever see its own day part-way through.
    expect(nextSweepAnchor(now)).toBe("2026-09-14");
    expect(nextSweepAnchor(at("2026-09-16T00:30:00+08:00"))).toBe("2026-09-15");
  });
  it("keeps rows from an already covered day out of the sweep scope", () => {
    // The anchor is the last day a sweep covered: 2026-09-14 was covered, so
    // today is the scope.
    const anchor = dateKey(Date.parse("2026-09-14T00:00:00+08:00"));
    expect(isWithinSweepScope(new Date("2026-09-15T09:20:00+08:00").toISOString(), anchor)).toBe(true);
    // Yesterday 23:51 is not, even though it is only minutes older: the sweep
    // compares the day, which is how BOSS labels the list.
    expect(isWithinSweepScope(new Date("2026-09-14T23:51:00+08:00").toISOString(), anchor)).toBe(false);
    // A date-only label is compared as that day too.
    expect(isWithinSweepScope(new Date("2026-09-14T00:00:00+08:00").toISOString(), anchor)).toBe(false);
    // A browser that missed days covers everything after the covered day.
    expect(isWithinSweepScope(new Date("2026-09-14T23:51:00+08:00").toISOString(), dateKey(Date.parse("2026-09-13T00:00:00+08:00")))).toBe(true);
    // With no anchor at all nothing is restricted.
    expect(isWithinSweepScope(new Date("2026-09-14T23:51:00+08:00").toISOString(), null)).toBe(true);
  });
  it("runs scheduled and manual catch-up in full history snapshot mode", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("chrome", { runtime: { sendMessage: vi.fn().mockResolvedValue({ ok: true }) } });
    const controller = new PageController(new CandidateSwitchAdapter());
    const startCatchup = vi.fn().mockResolvedValue(undefined);
    const internals = controller as unknown as {
      startCatchup: (historySnapshot?: boolean) => Promise<void>;
      scheduleCatchup: (delayMs: number) => void;
      runCatchupNow: () => Promise<void>;
    };
    internals.startCatchup = startCatchup;
    internals.scheduleCatchup(1_000);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(startCatchup).toHaveBeenCalledWith(true);
    startCatchup.mockClear();
    await internals.runCatchupNow();
    expect(startCatchup).toHaveBeenCalledWith(true);
    controller.stopCatchup();
  });
  it("refreshes promptly when a background tab becomes visible again", async () => {
    const sendMessage = vi.fn().mockResolvedValue({ ok: true, data: {} });
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const controller = new PageController(new CandidateSwitchAdapter());
    controller.start();
    const internals = controller as unknown as {
      catchupIntervalTimer?: number;
      stopVisibilityWatch?: () => void;
      watchConversationList: () => Promise<void>;
    };
    await eventually(() => !!internals.stopVisibilityWatch);
    // Coming back to the tab re-reads the conversation list once: the reply may
    // have been typed on another device while the tab was in the background.
    const refresh = vi.spyOn(internals, "watchConversationList").mockResolvedValue(undefined);
    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(refresh).not.toHaveBeenCalled();
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(internals.catchupIntervalTimer).toBeDefined();

    controller.stopCatchup();
    expect(internals.catchupIntervalTimer).toBeUndefined();
    expect(internals.stopVisibilityWatch).toBeUndefined();
    expect(
      (controller as unknown as { listWatchTimer?: number }).listWatchTimer,
    ).toBeUndefined();
  });
  it("renders a pushed duplicate alert once for the candidate currently open", async () => {
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) =>
      type === "GET_AUTH"
        ? Promise.resolve({ ok: true, data: { catchupEnabled: false } })
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
    const controller = new PageController(adapter);
    controller.start();
    await eventually(() =>
      sendMessage.mock.calls.some(([message]) => message.type === "CHECK_CONTEXT"),
    );

    const alert = {
      lookup_alert_id: "alert-1",
      notification_version: 1,
      candidate_name: adapter.candidate,
      job_name: "AI应用开发工程师",
      matched_recruiter_id: "other",
      matched_recruiter_name: "谢女士",
      match_level: "CONFIRMED_DUPLICATE",
      match_reason: "四项身份完全一致",
      last_activity_at: "2026-09-14T09:00:00.000Z",
    };
    // The pushed alert names a different candidate, so nothing is rendered.
    const unrelated = await controller.handleLiveAlert({ ...alert, candidate_name: "别的候选人" });
    expect(unrelated).toBe(false);

    const shown = await controller.handleLiveAlert(alert);
    expect(shown).toBe(true);
    // Re-delivery of the same version (the channel may retry) is ignored.
    expect(await controller.handleLiveAlert(alert)).toBe(false);
    // A stronger evidence version is a new fact and renders again.
    expect(await controller.handleLiveAlert({ ...alert, notification_version: 2 })).toBe(true);
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
    // An ordinary message only registers the event: the server asked for no
    // screenshot, so the page never enters the capture lifecycle.
    expect(
      sendMessage.mock.calls.some(
        ([message]) => message.type === "REPORT_SNAPSHOT_STATUS",
      ),
    ).toBe(false);
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
          : type === "GET_PLUGIN_SETTINGS"
            ? Promise.resolve({ ok: true, data: { catchup_enabled: false } })
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
  it("marks a manually clicked unread row as an unread synchronization", async () => {
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) =>
      type === "GET_AUTH"
        ? Promise.resolve({ ok: true, data: { catchupEnabled: false } })
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
    document.body.innerHTML = "<section><div id='unread'><span>1 今天 甲候选人 AI应用开发工程师</span></div></section>";
    const row = document.querySelector<HTMLElement>("#unread")!;
    Object.defineProperty(row, "innerText", { value: row.textContent, configurable: true });
    row.getBoundingClientRect = () => ({ x: 0, y: 40, top: 40, left: 0, right: 280, bottom: 100, width: 280, height: 60, toJSON: () => ({}) });
    const adapter = new CandidateSwitchAdapter();
    new PageController(adapter).start();
    await eventually(() => sendMessage.mock.calls.some(([message]) => message.type === "SYNC_CONVERSATION"));
    sendMessage.mockClear();
    row.querySelector("span")!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    adapter.callback();
    await eventually(() => sendMessage.mock.calls.some(
      ([message]) => message.type === "SYNC_CONVERSATION" &&
        message.payload.sync_reason === "UNREAD_CANDIDATE_OPENED",
    ));
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
  it("checks again when the open conversation moves under the recruiter", async () => {
    // The recruiter answered from a phone or another browser while this tab sat
    // on the candidate: the fingerprint is unchanged, but the conversation is
    // not — that is a new event and needs its own duplicate check.
    let updatedAt = "2026-09-02T02:00:00.000Z";
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) =>
      type === "GET_AUTH"
        ? Promise.resolve({ ok: true, data: { catchupEnabled: false } })
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
    adapter.extractCandidate = async () => ({
      status: "OK",
      value: { displayName: adapter.candidate, conversationUpdatedAt: updatedAt },
    });
    new PageController(adapter).start();
    const checks = () =>
      sendMessage.mock.calls.filter(([message]) => message.type === "CHECK_CONTEXT");
    await eventually(() => checks().length >= 1);
    expect(checks()).toHaveLength(1);

    // Repainting the same unchanged conversation must not re-check it.
    adapter.callback();
    await tick();
    await tick();
    expect(checks()).toHaveLength(1);

    updatedAt = "2026-09-02T09:15:00.000Z";
    adapter.callback();
    await eventually(() => checks().length >= 2);
    expect(checks().map(([message]) => message.payload.sync_reason)).toEqual([
      "CANDIDATE_OPENED",
      "CONVERSATION_UPDATED",
    ]);
    const moved = checks()[1][0].payload;
    expect(moved.candidate_display_name).toBe("甲候选人");
    expect(moved.conversation_updated_at).toBe("2026-09-02T09:15:00.000Z");
  });
  it("reports a sanitized diagnostic when a requested invitation snapshot cannot be captured", async () => {
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
                // The server asks for an image when this row still has no
                // usable capture; a send alone never asks for one.
                snapshot_needed: true,
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
      messageText: "您好，想和您聊聊岗位",
      statusEvidence: classifyBossOutgoingMessage("您好，想和您聊聊岗位"),
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
  it("never captures on a confirmed send, even when the server asks for one", async () => {
    // The history pass owns every capture. A send only registers the event, so
    // even a server that still answers `snapshot_needed: true` must not scroll
    // and photograph the pane the recruiter is typing in.
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) => {
      if (type === "GET_AUTH")
        return Promise.resolve({ ok: true, data: { catchupEnabled: false } });
      const data = {
        candidate_source_id: "source",
        result_type: "NO_HISTORY",
        ui: { severity: "success", title: "", message: "" },
        matches: [],
        available_actions: [],
        account_mapping: {},
        job_mapping: {},
        ...(type === "MESSAGE_SENT" ? { snapshot_needed: true } : {}),
      };
      return Promise.resolve({ ok: true, data });
    });
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const adapter = new CandidateSwitchAdapter();
    new PageController(adapter).start();
    await eventually(
      () =>
        sendMessage.mock.calls.filter(
          ([message]) => message.type === "SYNC_CONVERSATION",
        ).length >= 1,
    );
    // A passive page opening never captures, even when the sync response asks
    // for a history screenshot.
    expect(
      sendMessage.mock.calls.some(
        ([message]) => message.type === "REPORT_SNAPSHOT_STATUS",
      ),
    ).toBe(false);
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
    // An ordinary message: the registration succeeded and no capture ran.
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

    // The interview invitation is a send like any other: the long image stays
    // the history pass's job, so no capture is requested or attempted.
    adapter.messageCallback({
      sentAt: "2026-09-02T08:05:00.000Z",
      messageText: "您好，想和您约个面试时间",
      evidence: "DELIVERY_MARKER",
      statusEvidence: {
        status: "已约面",
        evidence: "BOSS_INTERVIEW_MARKER",
        ruleVersion: "boss-status-v4",
        observedAt: "2026-09-02T08:05:00.000Z",
      },
    });
    await eventually(
      () =>
        sendMessage.mock.calls.filter(
          ([message]) => message.type === "MESSAGE_SENT",
        ).length >= 2,
    );
    await tick();
    await tick();
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

describe("Read-only conversation list watcher", () => {
  const CHECK_RESPONSE = {
    candidate_source_id: null,
    result_type: "NO_HISTORY",
    ui: { severity: "success", title: "未发现重复", message: "" },
    matches: [],
    available_actions: [],
    account_mapping: {},
    job_mapping: {},
  };

  afterEach(() => {
    document.body.innerHTML = "";
    document.querySelector("#recruitment-collab-host")?.remove();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  function mountList(rows: string[]) {
    document.body.innerHTML = `<section>${rows.map((text) => `<div>${text}</div>`).join("")}</section>`;
    for (const item of document.querySelectorAll<HTMLElement>("section > div")) {
      Object.defineProperty(item, "innerText", { value: item.textContent, configurable: true });
      item.getBoundingClientRect = () => ({
        x: 0, y: 0, top: 0, left: 0, right: 280, bottom: 60,
        width: 280, height: 60, toJSON: () => ({}),
      });
    }
  }

  function stubChrome(store: Record<string, unknown>) {
    const sendMessage = vi.fn().mockImplementation(({ type }: { type: string }) =>
      type === "GET_AUTH"
        ? Promise.resolve({ ok: true, data: { catchupEnabled: true } })
        : type === "GET_PLUGIN_SETTINGS"
          ? Promise.resolve({ ok: true, data: { catchup_enabled: true } })
          : Promise.resolve({ ok: true, data: CHECK_RESPONSE }),
    );
    vi.stubGlobal("chrome", {
      runtime: { sendMessage },
      storage: {
        local: {
          get: (key: string, callback: (value: Record<string, unknown>) => void) =>
            callback(store[key] === undefined ? {} : { [key]: store[key] }),
          set: (value: Record<string, unknown>, callback?: () => void) => {
            Object.assign(store, value);
            callback?.();
          },
        },
      },
    });
    return sendMessage;
  }

  type WatchInternals = {
    watchConversationList: () => Promise<void>;
    listWatchWatermark: Map<string, string>;
    listWatchTimer?: number;
  };

  it("checks a row whose activity moved, without clicking or syncing it", async () => {
    mountList(["09:35 乙候选人 AI应用开发工程师"]);
    const store: Record<string, unknown> = {};
    const sendMessage = stubChrome(store);
    const clicks = vi.fn();
    document.querySelector("section")!.addEventListener("click", clicks);
    // A real, clickable “沟通中” filter: if the watcher ever switched filters
    // the way a catch-up pass does, this button would register the click.
    document.body.insertAdjacentHTML("afterbegin", "<button id='communicating'>沟通中</button>");
    const filter = document.querySelector<HTMLButtonElement>("#communicating")!;
    filter.getBoundingClientRect = () => ({
      x: 10, y: 20, top: 20, left: 10, right: 90, bottom: 52,
      width: 80, height: 32, toJSON: () => ({}),
    });
    const filterClicks = vi.fn();
    filter.addEventListener("click", filterClicks);
    const adapter = new CandidateSwitchAdapter();
    adapter.candidate = "甲候选人";
    const controller = new PageController(adapter);
    const dispose = controller.start();
    const internals = controller as unknown as WatchInternals;
    await eventually(() => !!internals.listWatchTimer);

    // First sight only records the row: an install must not fire one check per
    // conversation already in the list.
    await internals.watchConversationList();
    expect(
      sendMessage.mock.calls.filter(
        ([message]) =>
          message.type === "CHECK_CONTEXT" &&
          message.payload?.candidate_display_name === "乙候选人",
      ),
    ).toHaveLength(0);

    // A reply typed elsewhere moves the row's activity label.
    mountList(["09:41 乙候选人 AI应用开发工程师"]);
    await internals.watchConversationList();

    const probe = sendMessage.mock.calls
      .map(([message]) => message)
      .find(
        (message) =>
          message.type === "CHECK_CONTEXT" &&
          message.payload?.candidate_display_name === "乙候选人",
      );
    expect(probe).toBeDefined();
    expect(probe!.payload).toMatchObject({
      job_display_name: "AI应用开发工程师",
      account_display_name: "页面账号",
      sync_reason: "LIST_ACTIVITY_DETECTED",
      candidate_age: null,
      candidate_education: null,
      native_communications: [],
    });
    // The check is the whole feature: nothing here opens, reads or syncs the
    // conversation. The watched row is never synced, no send event is invented
    // and the server-side conversation index is never consulted.
    expect(clicks).not.toHaveBeenCalled();
    expect(filterClicks).not.toHaveBeenCalled();
    expect(
      sendMessage.mock.calls.some(
        ([message]) =>
          message.type === "SYNC_CONVERSATION" &&
          message.payload?.candidate_display_name === "乙候选人",
      ),
    ).toBe(false);
    expect(
      sendMessage.mock.calls.some(([message]) => message.type === "MESSAGE_SENT"),
    ).toBe(false);
    expect(
      sendMessage.mock.calls.some(
        ([message]) => message.type === "GET_CONVERSATION_INDEX",
      ),
    ).toBe(false);
    expect(store["boss-list-watch:页面账号"]).toBeDefined();

    // The same activity never probes twice.
    const before = sendMessage.mock.calls.length;
    await internals.watchConversationList();
    expect(sendMessage.mock.calls).toHaveLength(before);

    controller.stopCatchup();
    dispose();
  });

  it("leaves the open conversation to its own full check", async () => {
    mountList(["09:35 甲候选人 AI应用开发工程师"]);
    const store: Record<string, unknown> = {};
    const sendMessage = stubChrome(store);
    const adapter = new CandidateSwitchAdapter();
    adapter.candidate = "甲候选人";
    const controller = new PageController(adapter);
    const dispose = controller.start();
    const internals = controller as unknown as WatchInternals;
    await eventually(() => !!internals.listWatchTimer);
    // Let the open page's own check land so the fingerprint is known.
    await eventually(() =>
      sendMessage.mock.calls.some(
        ([message]) =>
          message.type === "CHECK_CONTEXT" &&
          message.payload?.sync_reason === "CANDIDATE_OPENED",
      ),
    );
    await internals.watchConversationList();
    sendMessage.mockClear();

    mountList(["09:41 甲候选人 AI应用开发工程师"]);
    await internals.watchConversationList();
    expect(
      sendMessage.mock.calls.some(
        ([message]) => message.payload?.sync_reason === "LIST_ACTIVITY_DETECTED",
      ),
    ).toBe(false);
    expect(internals.listWatchWatermark.size).toBeGreaterThan(0);

    controller.stopCatchup();
    dispose();
  });

  it("keeps the watermark on a failed probe so the next round retries", async () => {
    mountList(["09:35 乙候选人 AI应用开发工程师"]);
    const store: Record<string, unknown> = {};
    const sendMessage = stubChrome(store);
    sendMessage.mockImplementation(({ type }: { type: string }) =>
      type === "GET_AUTH"
        ? Promise.resolve({ ok: true, data: { catchupEnabled: true } })
        : type === "GET_PLUGIN_SETTINGS"
          ? Promise.resolve({ ok: true, data: { catchup_enabled: true } })
          : type === "CHECK_CONTEXT"
            ? Promise.resolve({ ok: false, error: "FEISHU_LOOKUP_UNAVAILABLE" })
            : Promise.resolve({ ok: true, data: CHECK_RESPONSE }),
    );
    const controller = new PageController(new CandidateSwitchAdapter());
    const dispose = controller.start();
    const internals = controller as unknown as WatchInternals;
    await eventually(() => !!internals.listWatchTimer);
    await internals.watchConversationList();
    mountList(["09:41 乙候选人 AI应用开发工程师"]);
    await internals.watchConversationList();
    const probes = () =>
      sendMessage.mock.calls.filter(
        ([message]) =>
          message.type === "CHECK_CONTEXT" &&
          message.payload?.candidate_display_name === "乙候选人",
      ).length;
    expect(probes()).toBe(1);
    // The failed probe is not recorded as seen, so it is retried.
    await internals.watchConversationList();
    expect(probes()).toBe(2);
    controller.stopCatchup();
    dispose();
  });

  it("retries a probe the API ignored instead of swallowing the row", async () => {
    mountList(["09:35 乙候选人 AI应用开发工程师"]);
    const store: Record<string, unknown> = {};
    const sendMessage = stubChrome(store);
    sendMessage.mockImplementation(({ type }: { type: string }) =>
      type === "GET_AUTH"
        ? Promise.resolve({ ok: true, data: { catchupEnabled: true } })
        : type === "GET_PLUGIN_SETTINGS"
          ? Promise.resolve({ ok: true, data: { catchup_enabled: true } })
          : type === "CHECK_CONTEXT"
            ? Promise.resolve({
                ok: true,
                data: { ...CHECK_RESPONSE, result_type: "IGNORED_PAGE" },
              })
            : Promise.resolve({ ok: true, data: CHECK_RESPONSE }),
    );
    const controller = new PageController(new CandidateSwitchAdapter());
    const dispose = controller.start();
    const internals = controller as unknown as WatchInternals;
    await eventually(() => !!internals.listWatchTimer);
    await internals.watchConversationList();
    mountList(["09:41 乙候选人 AI应用开发工程师"]);
    await internals.watchConversationList();
    const probes = () =>
      sendMessage.mock.calls.filter(
        ([message]) =>
          message.type === "CHECK_CONTEXT" &&
          message.payload?.candidate_display_name === "乙候选人",
      ).length;
    expect(probes()).toBe(1);
    await internals.watchConversationList();
    expect(probes()).toBe(2);
    controller.stopCatchup();
    dispose();
  });

  it("probes a row identity once when the list mounts it twice", async () => {
    mountList([
      "09:35 乙候选人 AI应用开发工程师",
      "09:35 乙候选人 AI应用开发工程师",
    ]);
    const store: Record<string, unknown> = {};
    const sendMessage = stubChrome(store);
    const controller = new PageController(new CandidateSwitchAdapter());
    const dispose = controller.start();
    const internals = controller as unknown as WatchInternals;
    await eventually(() => !!internals.listWatchTimer);
    await internals.watchConversationList();
    mountList([
      "09:41 乙候选人 AI应用开发工程师",
      "09:41 乙候选人 AI应用开发工程师",
    ]);
    await internals.watchConversationList();
    expect(
      sendMessage.mock.calls.filter(
        ([message]) =>
          message.type === "CHECK_CONTEXT" &&
          message.payload?.candidate_display_name === "乙候选人",
      ),
    ).toHaveLength(1);
    controller.stopCatchup();
    dispose();
  });
});
