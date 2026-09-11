import { afterEach, describe, expect, it, vi } from "vitest";
import {
  hasBossUnreadBadge,
  isBossListActivityDateOnly,
  isBossListActivityNewer,
  parseBossListActivity,
  runBossCatchup,
} from "../src/adapters/boss/boss-catchup";
describe("BOSS catch-up list times", () => {
  const now = new Date("2026-09-02T10:00:00+08:00");
  it("conservatively parses date-only and yesterday list entries", () => {
    expect(
      parseBossListActivity("振理 业务助理 08月05日", now)?.getMonth(),
    ).toBe(7);
    expect(parseBossListActivity("候选人 昨天", now)?.getDate()).toBe(1);
  });
  it("treats current time labels as current activity", () => {
    const parsed = parseBossListActivity("候选人 09:35", now)!;
    expect([parsed.getHours(), parsed.getMinutes()]).toEqual([9, 35]);
  });
  it("recognizes only a leading unread count as an unread badge", () => {
    expect(hasBossUnreadBadge("1 昨天 朱亮 ai应用开发工程师")).toBe(true);
    expect(hasBossUnreadBadge("昨天 朱亮 ai应用开发工程师 1")).toBe(false);
    expect(hasBossUnreadBadge("2 09月01日 候选人")).toBe(true);
  });
  it("distinguishes calendar-only labels from minute-precise activity", () => {
    expect(isBossListActivityDateOnly("09月05日 顾嘉雯 ai应用开发工程师")).toBe(true);
    expect(isBossListActivityDateOnly("昨天 秦永豪 ai应用开发工程师")).toBe(true);
    expect(isBossListActivityDateOnly("01:43 旷野途 ai应用开发工程师")).toBe(false);
    expect(isBossListActivityDateOnly("刚刚 旷野途 ai应用开发工程师")).toBe(false);
    expect(isBossListActivityDateOnly("昨天 23:51 旷野途 ai应用开发工程师")).toBe(false);
  });
  it("opens only when the BOSS activity is strictly newer than stored data", () => {
    expect(isBossListActivityNewer(
      "01:43 旷野途 ai应用开发工程师",
      "2026-09-07T01:43:00+08:00",
      "2026-09-07T01:42:00+08:00",
    )).toBe(true);
    expect(isBossListActivityNewer(
      "01:43 旷野途 ai应用开发工程师",
      "2026-09-07T01:43:00+08:00",
      "2026-09-07T01:43:30+08:00",
    )).toBe(false);
    expect(isBossListActivityNewer(
      "昨天 朱晓滢 ai应用开发工程师",
      "2026-09-06T00:00:00+08:00",
      "2026-09-07T00:00:03+08:00",
    )).toBe(false);
    expect(isBossListActivityNewer(
      "09月05日 顾嘉雯 ai应用开发工程师",
      "2026-09-05T00:00:00+08:00",
      "2026-09-04T23:59:00+08:00",
    )).toBe(true);
  });
});

describe("BOSS catch-up completion", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  function staticList() {
    document.body.innerHTML = "<section><div>甲候选人 09:35</div><div>乙候选人 昨天</div></section>";
    for (const item of document.querySelectorAll<HTMLElement>("section > div")) {
      Object.defineProperty(item, "innerText", {
        value: item.textContent,
        configurable: true,
      });
      item.getBoundingClientRect = () => ({
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        right: 280,
        bottom: 60,
        width: 280,
        height: 60,
        toJSON: () => ({}),
      });
    }
  }

  it("marks a static list complete only after every eligible item succeeds", async () => {
    staticList();
    const progress = vi.fn().mockResolvedValue(true);
    const result = await runBossCatchup(
      new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString(),
      "甲候选人",
      progress,
    );
    expect(result).toMatchObject({
      scanned: 2,
      available: true,
      complete: true,
    });
    expect(progress).toHaveBeenCalledTimes(2);
  });

  it("does not click or restore a conversation when logout cancels comparison", async () => {
    staticList();
    let cancelled = false;
    const clicks = vi.fn();
    document.querySelector("section")!.addEventListener("click", clicks);
    const progress = vi.fn().mockResolvedValue(true);
    const result = await runBossCatchup(new Date().toISOString(), "甲候选人", progress,
      async () => { cancelled = true; return true; }, undefined, () => cancelled);
    expect(result.complete).toBe(false);
    expect(clicks).not.toHaveBeenCalled();
    expect(progress).not.toHaveBeenCalled();
  });

  it("keeps the traversal incomplete after an item fails", async () => {
    staticList();
    const result = await runBossCatchup(
      new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString(),
      "甲候选人",
      vi.fn().mockResolvedValue(false),
    );
    expect(result).toMatchObject({
      scanned: 0,
      available: true,
      complete: false,
    });
  });

  it("finds rows nested under separate virtualization wrappers", async () => {
    document.body.innerHTML =
      "<section><div><div class='row'>甲候选人 09:35</div></div><div><div class='row'>乙候选人 昨天</div></div></section>";
    for (const item of document.querySelectorAll<HTMLElement>(".row")) {
      Object.defineProperty(item, "innerText", {
        value: item.textContent,
        configurable: true,
      });
      item.getBoundingClientRect = () => ({
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        right: 280,
        bottom: 60,
        width: 280,
        height: 60,
        toJSON: () => ({}),
      });
    }
    const progress = vi.fn().mockResolvedValue(true);
    const result = await runBossCatchup(
      new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString(),
      "",
      progress,
    );
    expect(result).toMatchObject({ scanned: 2, available: true, complete: true });
    expect(progress).toHaveBeenCalledTimes(2);
  });

  it("opens only rows whose list timestamp differs from the stored index", async () => {
    staticList();
    const progress = vi.fn().mockResolvedValue(true);
    const result = await runBossCatchup(
      new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString(),
      "",
      progress,
      (text) => text.includes("乙候选人"),
    );
    expect(result).toMatchObject({ scanned: 1, available: true, complete: true });
    expect(progress).toHaveBeenCalledTimes(1);
  });

  it("reports unread state before deciding whether a row needs opening", async () => {
    document.body.innerHTML = "<section><div>1 昨天 甲候选人 开发工程师</div><div>昨天 乙候选人 开发工程师</div></section>";
    for (const item of document.querySelectorAll<HTMLElement>("section > div")) {
      Object.defineProperty(item, "innerText", { value: item.textContent, configurable: true });
      item.getBoundingClientRect = () => ({ x: 0, y: 0, top: 0, left: 0, right: 280, bottom: 60, width: 280, height: 60, toJSON: () => ({}) });
    }
    const observations: Array<{ hasUnread: boolean }> = [];
    const result = await runBossCatchup(new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString(), "", vi.fn().mockResolvedValue(true), undefined, (observation) => { observations.push(observation); });
    expect(result.scanned).toBe(2);
    expect(observations.map((item) => item.hasUnread)).toEqual([true, false]);
  });

  it("does not drop a yesterday row after the checkpoint passes midnight", async () => {
    document.body.innerHTML = "<section><div>乙候选人 昨天</div><div>丙候选人 昨天</div></section>";
    for (const item of document.querySelectorAll<HTMLElement>("section > div")) {
      Object.defineProperty(item, "innerText", { value: item.textContent, configurable: true });
      item.getBoundingClientRect = () => ({ x: 0, y: 0, top: 0, left: 0, right: 280, bottom: 60, width: 280, height: 60, toJSON: () => ({}) });
    }
    const progress = vi.fn().mockResolvedValue(true);
    const result = await runBossCatchup(new Date().toISOString(), "", progress);
    expect(result.complete).toBe(true);
    expect(progress).toHaveBeenCalledTimes(2);
  });

  it("reconciles activity beyond the old rolling window", async () => {
    const withinWindow = new Date(Date.now() - 47 * 60 * 60 * 1000);
    const dateLabel = `${withinWindow.getFullYear()}-${String(withinWindow.getMonth() + 1).padStart(2, "0")}-${String(withinWindow.getDate()).padStart(2, "0")}`;
    document.body.innerHTML =
      `<section><div>${dateLabel} 甲候选人 开发工程师</div><div>${dateLabel} 乙候选人 开发工程师</div></section>`;
    for (const item of document.querySelectorAll<HTMLElement>("section > div")) {
      Object.defineProperty(item, "innerText", { value: item.textContent, configurable: true });
      item.getBoundingClientRect = () => ({ x: 0, y: 0, top: 0, left: 0, right: 280, bottom: 60, width: 280, height: 60, toJSON: () => ({}) });
    }
    const progress = vi.fn().mockResolvedValue(true);
    const result = await runBossCatchup(
      new Date().toISOString(),
      "",
      progress,
    );
    expect(result).toMatchObject({ scanned: 2, available: true, complete: true });
    expect(progress).toHaveBeenCalledTimes(2);
    expect(progress.mock.calls[0][1]).toContain("甲候选人");
  });

  it("falls back to text rows when a virtualized wrapper reports zero geometry", async () => {
    document.body.innerHTML = "<section><div><span>甲候选人 10:15</span></div><div><span>乙候选人 10:16</span></div></section>";
    for (const item of document.querySelectorAll<HTMLElement>("section > div"))
      Object.defineProperty(item, "innerText", { value: item.textContent, configurable: true });
    const progress = vi.fn().mockResolvedValue(true);
    const result = await runBossCatchup(new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString(), "", progress);
    expect(result).toMatchObject({ scanned: 2, available: true, complete: true });
  });
});
