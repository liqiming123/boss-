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
  it("observes resume preview clicks only inside the active conversation region", async () => {
    vi.useFakeTimers();
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
