import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { dispatchDailyClick } from "../src/background/daily-real-click";
import { dailyMessage } from "../src/background/company-daily";
vi.mock("../src/background/auth-store", () => ({ getAuth: async () => ({ accessToken: "test-only" }) }));
vi.mock("../src/background/api-client", () => ({ apiRequest: vi.fn() }));
const url = "https://zhipin.com/web/frame/enterprise/recruit/data";
beforeEach(() => {
  vi.stubGlobal("chrome", {
    storage: { local: { get: vi.fn(async () => ({ companyDailyJob: { tabId: 7 } })) } },
    tabs: { get: vi.fn(async () => ({ id: 7, active: true, url })) },
    debugger: { attach: vi.fn(async () => undefined), detach: vi.fn(async () => undefined), sendCommand: vi.fn(async (_target, method) => method === "Page.getLayoutMetrics" ? { cssLayoutViewport: { clientWidth: 800, clientHeight: 600 } } : {}) },
  });
});
afterEach(() => vi.unstubAllGlobals());
describe("authorized daily debugger click", () => {
  it("rejects other tabs and subframe senders before debugger attachment", async () => {
    for (const sender of [{ tab: { id: 8 }, frameId: 0, url }, { tab: { id: 7 }, frameId: 1, url }]) {
      await expect(dailyMessage("DAILY_REAL_CLICK", { x: 10, y: 10 }, sender as chrome.runtime.MessageSender)).rejects.toThrow("DAILY_JOB_NOT_AUTHORIZED");
    }
    expect(chrome.debugger.attach).not.toHaveBeenCalled();
  });
  it("dispatches all mouse events to the authorized report tab and detaches", async () => {
    await dailyMessage("DAILY_REAL_CLICK", { x: 10, y: 20 }, { tab: { id: 7 }, frameId: 0, url } as chrome.runtime.MessageSender);
    expect(chrome.debugger.sendCommand).toHaveBeenCalledWith({ tabId: 7 }, "Input.dispatchMouseEvent", expect.objectContaining({ type: "mouseReleased", x: 10, y: 20 }));
    expect(chrome.debugger.detach).toHaveBeenCalledWith({ tabId: 7 });
  });
  it("rejects nonfinite and out-of-viewport coordinates", async () => {
    await expect(dispatchDailyClick(7, { x: NaN, y: 2 })).rejects.toThrow("DAILY_CLICK_INVALID");
    expect(chrome.debugger.attach).not.toHaveBeenCalled();
    await expect(dispatchDailyClick(7, { x: 801, y: 2 })).rejects.toThrow("DAILY_CLICK_OUTSIDE_VIEWPORT");
    expect(chrome.debugger.detach).toHaveBeenCalled();
  });
  it("fails without stealing focus when the user has switched tabs", async () => {
    vi.mocked(chrome.tabs.get).mockResolvedValue({ active: false, url } as chrome.tabs.Tab);
    await expect(dispatchDailyClick(7, { x: 10, y: 20 })).rejects.toThrow("DAILY_TAB_NOT_ACTIVE");
    expect(chrome.debugger.attach).not.toHaveBeenCalled();
  });
  it("releases the mouse and debugger after a pressed-event failure", async () => {
    vi.mocked(chrome.debugger.sendCommand).mockImplementation(async (_target, method, params: any) => {
      if (method === "Page.getLayoutMetrics") return { cssLayoutViewport: { clientWidth: 800, clientHeight: 600 } };
      if (params.type === "mousePressed") throw new Error("transport failed");
      return {};
    });
    await expect(dispatchDailyClick(7, { x: 10, y: 20 })).rejects.toThrow("DAILY_CLICK_FAILED");
    expect(chrome.debugger.sendCommand).toHaveBeenCalledWith({ tabId: 7 }, "Input.dispatchMouseEvent", expect.objectContaining({ type: "mouseReleased" }));
    expect(chrome.debugger.detach).toHaveBeenCalled();
  });
});
