import { isReportUrl } from "../adapters/boss/company-daily";

const clicking = new Set<number>();
// Called only after dailyMessage has checked the stored job, login and sender.
export async function dispatchDailyClick(tabId: number, payload: unknown): Promise<void> {
  const p = payload as { x?: unknown; y?: unknown } | undefined;
  if (typeof p?.x !== "number" || typeof p.y !== "number" || !Number.isFinite(p.x) || !Number.isFinite(p.y) || p.x < 0 || p.y < 0) throw new Error("DAILY_CLICK_INVALID");
  if (clicking.has(tabId)) throw new Error("DAILY_CLICK_BUSY");
  clicking.add(tabId);
  const target = { tabId };
  let attached = false;
  try {
    const tab = await chrome.tabs.get(tabId);
    if (!isReportUrl(tab.url)) throw new Error("DAILY_JOB_NOT_AUTHORIZED");
    if (!tab.active) throw new Error("DAILY_TAB_NOT_ACTIVE");
    try { await chrome.debugger.attach(target, "1.3"); attached = true; }
    catch { throw new Error("DAILY_DEBUGGER_UNAVAILABLE"); }
    const metrics = await chrome.debugger.sendCommand(target, "Page.getLayoutMetrics") as { cssLayoutViewport?: { clientWidth: number; clientHeight: number } };
    const viewport = metrics.cssLayoutViewport;
    if (!viewport || p.x >= viewport.clientWidth || p.y >= viewport.clientHeight) throw new Error("DAILY_CLICK_OUTSIDE_VIEWPORT");
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", { type: "mouseMoved", x: p.x, y: p.y });
    try {
      await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", { type: "mousePressed", x: p.x, y: p.y, button: "left", buttons: 1, clickCount: 1 });
    } finally {
      await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", { type: "mouseReleased", x: p.x, y: p.y, button: "left", buttons: 0, clickCount: 1 });
    }
  } catch (error) {
    if (error instanceof Error && /^DAILY_[A-Z_]+$/.test(error.message)) throw error;
    throw new Error("DAILY_CLICK_FAILED");
  } finally {
    if (attached) await chrome.debugger.detach(target).catch(() => undefined);
    clicking.delete(tabId);
  }
}
