import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ensureDailyPeriod, exact, selectReportDate } from "../src/adapters/boss/company-daily";
import { clickDailyControl } from "../src/shared/daily-click";

vi.mock("../src/shared/delay", () => ({ delay: async () => undefined }));
const rect = { left: 10, top: 20, width: 80, height: 30, right: 90, bottom: 50, x: 10, y: 20, toJSON() {} };

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(rect);
  vi.stubGlobal("chrome", { runtime: { sendMessage: vi.fn() } });
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
  Object.defineProperty(document, "elementFromPoint", { configurable: true, value: vi.fn(() => document.querySelector("button")) });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.innerHTML = ""; });

describe("daily period interaction", () => {
  it("ignores a hidden day option and actually opens the visible range menu before selecting day", async () => {
    document.body.innerHTML = '<button>近七天</button><div hidden><span>按天查看</span></div>';
    expect(exact(document, "按天查看")).toBeUndefined();
    const calls: string[] = [];
    vi.mocked(chrome.runtime.sendMessage).mockImplementation(async (message: any) => {
      expect(message.type).toBe("DAILY_REAL_CLICK");
      calls.push(document.querySelector("button")!.textContent!);
      if (calls.length === 1) document.body.innerHTML = '<button>按天查看</button>';
      else document.body.innerHTML = '<button>按天查看</button><input placeholder="选择日期" value="2026-09-08">';
      return { ok: true };
    });
    expect(await ensureDailyPeriod(async () => undefined)).toBe(document.querySelector("input"));
    expect(calls).toEqual(["近七天", "按天查看"]);
  });
  it("reports a failed menu click without proceeding to a calendar", async () => {
    document.body.innerHTML = '<button>近七天</button><div hidden>按天查看</div>';
    vi.mocked(chrome.runtime.sendMessage).mockResolvedValue({ ok: true });
    await expect(ensureDailyPeriod(async () => undefined)).rejects.toThrow("DAILY_PERIOD_MENU_NOT_OPEN");
    expect(chrome.runtime.sendMessage).toHaveBeenCalledTimes(1);
  });
  it("requires the new date control after day selection", async () => {
    document.body.innerHTML = '<button>按天查看</button><table></table>';
    await expect(ensureDailyPeriod(async () => undefined)).rejects.toThrow("DAILY_PERIOD_SWITCH_FAILED");
  });
  it("does not replay a click on a lost response", async () => {
    document.body.innerHTML = '<button>近七天</button>';
    vi.mocked(chrome.runtime.sendMessage).mockRejectedValue(new Error("lost response"));
    await expect(clickDailyControl(document.querySelector("button")!)).rejects.toThrow("lost response");
    expect(chrome.runtime.sendMessage).toHaveBeenCalledTimes(1);
  });
  it("does not click a control covered by an overlay", async () => {
    document.body.innerHTML = '<button>近七天</button><div>overlay</div>';
    vi.mocked(document.elementFromPoint).mockReturnValue(document.querySelector("div"));
    await expect(clickDailyControl(document.querySelector("button")!)).rejects.toThrow("DAILY_CONTROL_OBSCURED");
    expect(chrome.runtime.sendMessage).not.toHaveBeenCalled();
  });
});


describe("daily calendar interaction", () => {
  it("opens the picker, verifies year/month and clicks the correct day via CDP", async () => {
    document.body.innerHTML = '<button>2026-09-09</button>';
    const targets: string[] = [];
    vi.mocked(document.elementFromPoint).mockImplementation(() => targets.length ? document.querySelectorAll("td")[7] : document.querySelector("button"));
    vi.mocked(chrome.runtime.sendMessage).mockImplementation(async () => {
      targets.push(targets.length ? document.querySelectorAll("td")[7].textContent! : "date-control");
      document.body.innerHTML = '<span role="button">2026 年</span><span role="button">9 月</span><table><tr>' + Array.from({length:30}, (_,i) => `<td>${i+1}</td>`).join("") + '</tr></table>';
      return { ok: true };
    });
    await selectReportDate(document, document.querySelector("button")!, "2026-09-08", async () => undefined);
    expect(targets).toEqual(["date-control", "8"]);
  });
  it("never picks a day from an unverified month", async () => {
    document.body.innerHTML = '<table><tr>' + Array.from({length:30}, (_,i) => `<td>${i+1}</td>`).join("") + '</tr></table>';
    await expect(selectReportDate(document, undefined, "2026-09-08", async () => undefined)).rejects.toThrow("DAILY_CALENDAR_MONTH_UNVERIFIED");
    expect(chrome.runtime.sendMessage).not.toHaveBeenCalled();
  });
});
