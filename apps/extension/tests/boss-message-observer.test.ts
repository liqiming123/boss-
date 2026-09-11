import { afterEach, describe, expect, it, vi } from "vitest";
import {
  observeBossRecruiterMessageSent,
  observeBossResumePreviewOpened,
} from "../src/adapters/boss/boss-message-observer";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("BOSS recruiter message observer", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });
  it("emits only after a send gesture produces outgoing delivery evidence", async () => {
    document.body.innerHTML =
      '<textarea></textarea><button aria-label="发送">发送</button>';
    Object.defineProperty(document.body, "innerText", {
      configurable: true,
      get: () => document.body.textContent || "",
    });
    const callback = vi.fn(),
      stop = observeBossRecruiterMessageSent(callback),
      editor = document.querySelector("textarea")!;
    editor.value = "仅在内存中确认的消息";
    editor.focus();
    document.querySelector("button")!.click();
    expect(callback).not.toHaveBeenCalled();
    editor.value = "";
    const bubble = document.createElement("div");
    bubble.textContent = "仅在内存中确认的消息 送达";
    document.body.append(bubble);
    await tick();
    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback.mock.calls[0][0].evidence).toBe("DELIVERY_MARKER");
    expect(callback.mock.calls[0][0].messageText).toBe(
      "仅在内存中确认的消息",
    );
    stop();
  });
  it("carries a confirmed rejection draft and its status evidence", async () => {
    document.body.innerHTML =
      '<textarea></textarea><button aria-label="发送">发送</button>';
    Object.defineProperty(document.body, "innerText", {
      configurable: true,
      get: () => document.body.textContent || "",
    });
    const callback = vi.fn();
    const stop = observeBossRecruiterMessageSent(callback);
    const editor = document.querySelector("textarea")!;
    editor.value = "您的经历与岗位不匹配，这次先不推进了";
    editor.focus();
    document.querySelector("button")!.click();
    editor.value = "";
    const bubble = document.createElement("div");
    bubble.textContent = "您的经历与岗位不匹配，这次先不推进了 送达";
    document.body.append(bubble);
    await tick();
    expect(callback.mock.calls[0][0].statusEvidence.status).toBe("已拒绝");
    stop();
  });
  it("ignores candidate messages when no recruiter send gesture occurred", async () => {
    document.body.innerHTML = "<textarea></textarea>";
    Object.defineProperty(document.body, "innerText", {
      configurable: true,
      get: () => document.body.textContent || "",
    });
    const callback = vi.fn(),
      stop = observeBossRecruiterMessageSent(callback);
    const inbound = document.createElement("div");
    inbound.textContent = "候选人新消息";
    document.body.append(inbound);
    await tick();
    expect(callback).not.toHaveBeenCalled();
    stop();
  });
  it.each(["您好，想和您聊聊这个岗位", "前几天聊过，方便明天面试吗"])(
    "detects proactive sends after the send button takes focus: %s", async (draft) => {
      document.body.innerHTML = '<textarea></textarea><button aria-label="发送">发送</button>';
      Object.defineProperty(document.body, "innerText", {
        configurable: true, get: () => document.body.textContent || "",
      });
      const callback = vi.fn();
      const stop = observeBossRecruiterMessageSent(callback);
      const editor = document.querySelector("textarea")!;
      const button = document.querySelector("button")!;
      editor.focus();
      editor.value = draft;
      button.focus();
      button.click();
      expect(callback).not.toHaveBeenCalled();
      editor.value = "";
      const bubble = document.createElement("div");
      bubble.textContent = `${draft} 送达`;
      document.body.append(bubble);
      await tick();
      expect(callback).toHaveBeenCalledTimes(1);
      expect(callback.mock.calls[0][0].statusEvidence.status).toBe(
        draft.includes("面试") ? "待约面" : "沟通中",
      );
      stop();
    },
  );
  it("records a dialog-sent interview invitation without a chat draft", async () => {
    Object.defineProperty(document.documentElement, "clientWidth", {
      configurable: true,
      value: 1200,
    });
    document.body.innerHTML =
      '<main><p>沟通职位：短视频编导</p><textarea></textarea><button id="invite" aria-label="约面试">约面试</button></main>';
    Object.defineProperty(document.body, "innerText", {
      configurable: true,
      get: () => document.body.textContent || "",
    });
    const region = document.querySelector("main")!;
    vi.spyOn(region, "getBoundingClientRect").mockReturnValue({
      left: 420,
      right: 1120,
      top: 100,
      bottom: 800,
      width: 700,
      height: 700,
      x: 420,
      y: 100,
      toJSON: () => ({}),
    });
    const callback = vi.fn();
    const stop = observeBossRecruiterMessageSent(callback);
    document.querySelector<HTMLButtonElement>("#invite")!.click();
    // BOSS opens the scheduler. That mutation must never be mistaken for the
    // button label being sent as a chat message.
    const scheduler = document.createElement("div");
    scheduler.id = "scheduler";
    scheduler.innerHTML =
      '<p>面试时间：2026-09-12 14:00</p><button id="confirm" aria-label="确认">确认</button>';
    document.body.append(scheduler);
    await tick();
    expect(callback).not.toHaveBeenCalled();
    // The scheduler is rendered outside the chat pane and confirms without any
    // draft text; it must still be recognized as the invitation.
    document.querySelector<HTMLButtonElement>("#confirm")!.click();
    await tick();
    expect(callback).not.toHaveBeenCalled();
    const marker = document.createElement("div");
    marker.textContent = "面试邀请已发送";
    document.body.append(marker);
    await tick();
    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback.mock.calls[0][0].messageText).toBeUndefined();
    expect(callback.mock.calls[0][0].statusEvidence).toMatchObject({
      status: "已约面",
      evidence: "BOSS_INTERVIEW_INVITE",
    });
    stop();
  });
  it("records the real dialog flow: div 约面试 -> 发送 -> 发送了面试邀请", async () => {
    document.body.innerHTML =
      '<div id="toolbar"><span id="invite">约面试</span></div><textarea></textarea>';
    Object.defineProperty(document.body, "innerText", {
      configurable: true,
      get: () => document.body.textContent || "",
    });
    const invite = document.querySelector<HTMLElement>("#invite")!;
    // BOSS renders the quick-action bar as plain divs/spans, and jsdom has no
    // innerText, so expose the same visible label the browser would see.
    Object.defineProperty(invite, "innerText", {
      configurable: true,
      get: () => "约面试",
    });
    const callback = vi.fn();
    const stop = observeBossRecruiterMessageSent(callback);
    invite.click();
    const dialog = document.createElement("div");
    dialog.innerHTML =
      "<p>线下面试邀请</p><p>面试地址：无锡梁溪区世金中心39层</p><p>面试时间：选择日期 选择开始时间</p><button id=\"confirm\" aria-label=\"发送\">发送</button>";
    document.body.append(dialog);
    await tick();
    expect(callback).not.toHaveBeenCalled();
    document.querySelector<HTMLButtonElement>("#confirm")!.click();
    const bubble = document.createElement("div");
    bubble.textContent = "发送了面试邀请";
    document.body.append(bubble);
    await tick();
    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback.mock.calls[0][0].messageText).toBeUndefined();
    expect(callback.mock.calls[0][0].statusEvidence).toMatchObject({
      status: "已约面",
      evidence: "BOSS_INTERVIEW_INVITE",
    });
    stop();
  });
  it("carries the scheduled interview through the invitation event", async () => {
    document.body.innerHTML =
      '<div id="toolbar"><span id="invite">约面试</span></div><textarea></textarea>';
    Object.defineProperty(document.body, "innerText", {
      configurable: true,
      get: () => document.body.textContent || "",
    });
    const invite = document.querySelector<HTMLElement>("#invite")!;
    Object.defineProperty(invite, "innerText", {
      configurable: true,
      get: () => "约面试",
    });
    const callback = vi.fn();
    const stop = observeBossRecruiterMessageSent(callback);
    invite.click();
    const dialog = document.createElement("div");
    dialog.innerHTML =
      "<p>线下面试邀请</p>" +
      '<label><input type="radio" name="t" checked /> 线下面试</label>' +
      '<label><input type="radio" name="t" /> 线上面试</label>' +
      '<div><span>面试地址</span><input value="无锡梁溪区世金中心39层" /></div>' +
      '<div><span>面试时间</span><input value="2026-09-12" /><input value="14:00" /></div>' +
      '<button id="confirm" aria-label="发送">发送</button>';
    document.body.append(dialog);
    await tick();
    document.querySelector<HTMLButtonElement>("#confirm")!.click();
    const bubble = document.createElement("div");
    bubble.textContent = "发送了面试邀请";
    document.body.append(bubble);
    await tick();
    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback.mock.calls[0][0].interview).toEqual({
      interview_type: "OFFLINE",
      scheduled_at: "2026-09-12T14:00:00+08:00",
      location: "无锡梁溪区世金中心39层",
    });
    stop();
  });
  it("still records 已约面 when the schedule cannot be read", async () => {
    document.body.innerHTML =
      '<div id="toolbar"><span id="invite">约面试</span></div><textarea></textarea>';
    Object.defineProperty(document.body, "innerText", {
      configurable: true,
      get: () => document.body.textContent || "",
    });
    const invite = document.querySelector<HTMLElement>("#invite")!;
    Object.defineProperty(invite, "innerText", {
      configurable: true,
      get: () => "约面试",
    });
    const callback = vi.fn();
    const stop = observeBossRecruiterMessageSent(callback);
    invite.click();
    const dialog = document.createElement("div");
    // The recruiter confirmed without a readable date/time. The invitation must
    // still be recognized and synchronized; only the calendar entry is skipped.
    dialog.innerHTML =
      "<p>线下面试邀请</p>" +
      '<label><input type="radio" name="t" checked /> 线下面试</label>' +
      '<div><span>面试时间</span><input placeholder="选择日期" value="" /><input placeholder="选择开始时间" value="" /></div>' +
      '<button id="confirm" aria-label="发送">发送</button>';
    document.body.append(dialog);
    await tick();
    document.querySelector<HTMLButtonElement>("#confirm")!.click();
    const bubble = document.createElement("div");
    bubble.textContent = "发送了面试邀请";
    document.body.append(bubble);
    await tick();
    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback.mock.calls[0][0].statusEvidence).toMatchObject({
      status: "已约面",
      evidence: "BOSS_INTERVIEW_INVITE",
    });
    expect(callback.mock.calls[0][0].interview?.scheduled_at).toBeUndefined();
    stop();
  });
  it("records the invitation when the scheduler closes without a text marker", async () => {
    Object.defineProperty(document.documentElement, "clientWidth", {
      configurable: true,
      value: 1200,
    });
    document.body.innerHTML =
      '<main><p>沟通职位：短视频编导</p><textarea></textarea><button id="invite" aria-label="约面试">约面试</button></main>' +
      '<div id="scheduler"><p>面试时间：2026-09-12 14:00</p><button id="confirm" aria-label="确认">确认</button></div>';
    Object.defineProperty(document.body, "innerText", {
      configurable: true,
      get: () => document.body.textContent || "",
    });
    const region = document.querySelector("main")!;
    vi.spyOn(region, "getBoundingClientRect").mockReturnValue({
      left: 420,
      right: 1120,
      top: 100,
      bottom: 800,
      width: 700,
      height: 700,
      x: 420,
      y: 100,
      toJSON: () => ({}),
    });
    const callback = vi.fn();
    const stop = observeBossRecruiterMessageSent(callback);
    document.querySelector<HTMLButtonElement>("#invite")!.click();
    document.querySelector<HTMLButtonElement>("#confirm")!.click();
    // A validation failure keeps the dialog open; closing it is the only signal
    // that the invitation actually went out.
    await new Promise((resolve) => setTimeout(resolve, 1_100));
    document.querySelector("#scheduler")!.remove();
    await tick();
    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback.mock.calls[0][0].statusEvidence).toMatchObject({
      status: "已约面",
      evidence: "BOSS_INTERVIEW_INVITE",
    });
    stop();
  });
  it("records 约面 even when BOSS renders the toolbar outside the chat pane", async () => {
    Object.defineProperty(document.documentElement, "clientWidth", {
      configurable: true,
      value: 1200,
    });
    document.body.innerHTML =
      '<main><p>沟通职位：业务助理/总经理助理</p><textarea></textarea></main>' +
      '<div id="toolbar"><button id="invite" aria-label="约面试">约面试</button></div>';
    Object.defineProperty(document.body, "innerText", {
      configurable: true,
      get: () => document.body.textContent || "",
    });
    const region = document.querySelector("main")!;
    vi.spyOn(region, "getBoundingClientRect").mockReturnValue({
      left: 420,
      right: 1120,
      top: 100,
      bottom: 800,
      width: 700,
      height: 700,
      x: 420,
      y: 100,
      toJSON: () => ({}),
    });
    const callback = vi.fn();
    const stop = observeBossRecruiterMessageSent(callback);
    // Step 1: the entry click is outside the detected pane and must still be
    // remembered, otherwise the confirmation below has nothing to attach to.
    document.querySelector<HTMLButtonElement>("#invite")!.click();
    const scheduler = document.createElement("div");
    scheduler.id = "scheduler";
    scheduler.innerHTML =
      '<p>线下面试邀请</p><p>面试时间：选择日期 选择开始时间</p><button id="confirm" aria-label="发送">发送</button>';
    document.body.append(scheduler);
    await tick();
    expect(callback).not.toHaveBeenCalled();
    // Step 2: the dialog confirmation, also outside the pane.
    document.querySelector<HTMLButtonElement>("#confirm")!.click();
    const bubble = document.createElement("div");
    bubble.textContent = "发送了面试邀请";
    document.body.append(bubble);
    await tick();
    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback.mock.calls[0][0].statusEvidence).toMatchObject({
      status: "已约面",
      evidence: "BOSS_INTERVIEW_INVITE",
    });
    stop();
  });
  it("does not turn an ordinary send into an invitation after a cancelled 约面", async () => {
    document.body.innerHTML =
      '<textarea></textarea><button id="send" aria-label="发送">发送</button><button id="invite" aria-label="约面试">约面试</button>';
    Object.defineProperty(document.body, "innerText", {
      configurable: true,
      get: () => document.body.textContent || "",
    });
    const callback = vi.fn();
    const stop = observeBossRecruiterMessageSent(callback);
    const editor = document.querySelector("textarea")!;
    // The recruiter opens the scheduler and cancels it.
    document.querySelector<HTMLButtonElement>("#invite")!.click();
    editor.value = "您好，想和您聊聊岗位";
    editor.focus();
    document.querySelector<HTMLButtonElement>("#send")!.click();
    editor.value = "";
    const bubble = document.createElement("div");
    bubble.textContent = "您好，想和您聊聊岗位 送达";
    document.body.append(bubble);
    await tick();
    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback.mock.calls[0][0].messageText).toBe("您好,想和您聊聊岗位");
    expect(callback.mock.calls[0][0].statusEvidence.status).toBe("沟通中");
    stop();
  });
  it("observes resume preview clicks only inside the active conversation region", async () => {    vi.useFakeTimers();
    Object.defineProperty(document.documentElement, "clientWidth", {
      configurable: true,
      value: 1200,
    });
    document.body.innerHTML =
      '<button id="outside" aria-label="查看附件简历">查看附件简历</button><main><p>沟通职位：短视频编导</p><button id="inside" aria-label="查看附件简历">查看附件简历</button></main>';
    const region = document.querySelector("main")!;
    vi.spyOn(region, "getBoundingClientRect").mockReturnValue({
      left: 420,
      right: 1120,
      top: 100,
      bottom: 800,
      width: 700,
      height: 700,
      x: 420,
      y: 100,
      toJSON: () => ({}),
    });
    const callback = vi.fn();
    const stop = observeBossResumePreviewOpened(callback);
    document.querySelector<HTMLButtonElement>("#outside")!.click();
    await vi.runAllTimersAsync();
    expect(callback).not.toHaveBeenCalled();
    document.querySelector<HTMLButtonElement>("#inside")!.click();
    await vi.runAllTimersAsync();
    expect(callback).toHaveBeenCalledTimes(1);
    stop();
    vi.useRealTimers();
  });
});
