import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  bossRowIdentity,
  hasBossUnreadBadge,
  isBossListActivityDateOnly,
  isBossListActivityNewer,
  observeBossUnreadConversationClick,
  openBossCommunicatingFilter,
  parseBossListActivity,
  parseBossRowIdentity,
  readBossMountedRows,
  runBossCatchup,
  currentBossListFilter,
  decideBossRow,
  restoreBossListFilter,
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
  it("captures unread state before a conversation row click consumes it", () => {
    document.body.innerHTML = "<section><div id='row'><span>2 今天 甲候选人 开发工程师</span></div></section>";
    const row = document.querySelector<HTMLElement>("#row")!;
    Object.defineProperty(row, "innerText", { value: row.textContent, configurable: true });
    row.getBoundingClientRect = () => ({ x: 0, y: 40, top: 40, left: 0, right: 280, bottom: 100, width: 280, height: 60, toJSON: () => ({}) });
    const seen: string[] = [];
    const stop = observeBossUnreadConversationClick((text) => seen.push(text));
    row.querySelector("span")!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    stop();
    expect(seen).toEqual(["2 今天 甲候选人 开发工程师"]);
  });
  it("selects the visible top-level communicating filter", async () => {
    document.body.innerHTML = "<button id='all'>全部候选人</button><button id='communicating'>沟通中</button>";
    const button = document.querySelector<HTMLButtonElement>("#communicating")!;
    button.getBoundingClientRect = () => ({ x: 10, y: 20, top: 20, left: 10, right: 90, bottom: 52, width: 80, height: 32, toJSON: () => ({}) });
    const clicked = vi.fn();
    button.addEventListener("click", clicked);
    expect(await openBossCommunicatingFilter()).toBe(true);
    expect(clicked).toHaveBeenCalledTimes(1);
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

describe("conversation filter restore", () => {
  const row = (id: string, label: string, selected = false) => {
    const element = document.createElement("div");
    element.id = id;
    element.textContent = label;
    if (selected) element.setAttribute("aria-selected", "true");
    element.getBoundingClientRect = () =>
      ({ x: 0, y: 40, top: 40, left: 0, right: 60, bottom: 70, width: 60, height: 30, toJSON: () => ({}) }) as DOMRect;
    document.body.append(element);
    return element;
  };
  /** A real tab strip moves the selected marker to whatever was clicked. */
  const selectsOnClick = (element: HTMLElement) => {
    element.addEventListener("click", () => {
      for (const node of document.querySelectorAll("[aria-selected]"))
        node.removeAttribute("aria-selected");
      element.setAttribute("aria-selected", "true");
    });
  };

  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("reports the filter the recruiter is reading", () => {
    row("tab-all", "全部");
    row("tab-com", "沟通中", true);
    expect(currentBossListFilter()).toBe("沟通中");
  });

  it("does not guess when no filter is marked selected", () => {
    row("tab-all", "全部");
    expect(currentBossListFilter()).toBeUndefined();
  });

  it("finds a tab whose label carries a badge count", () => {
    row("tab-new-plain", "新招呼 3");
    expect(currentBossListFilter()).toBeUndefined();
    // The same label can be rendered twice (a stale node plus the live tab);
    // the selected one is what counts, not the one that looks smallest.
    row("tab-new-selected", "新招呼3", true);
    expect(currentBossListFilter()).toBe("新招呼");
    row("tab-com", "沟通中(0)");
    expect(currentBossListFilter()).toBe("新招呼");
  });

  it("recognises a hashed CSS-module marker and a nested label marker", () => {
    const hashed = row("tab-new", "新招呼");
    hashed.className = "tabItem_active__1a2b3";
    row("tab-all", "全部");
    expect(currentBossListFilter()).toBe("新招呼");

    document.body.innerHTML = "";
    const wrapper = row("tab-interview", "已约面");
    const label = document.createElement("span");
    label.className = "text--selected";
    label.textContent = "已约面";
    wrapper.textContent = "";
    wrapper.append(label);
    expect(currentBossListFilter()).toBe("已约面");

    document.body.innerHTML = "";
    const nested = row("tab-offer", "已获取简历");
    nested.className = "inactive"; // must never read as selected
    expect(currentBossListFilter()).toBeUndefined();
  });

  it("returns the recruiter to a non-default filter", async () => {
    // A pass forces “沟通中”; the recruiter was on “新招呼”, which the pass
    // must restore or they are left staring at the wrong list.
    const target = row("tab-new", "新招呼");
    row("tab-com", "沟通中", true);
    selectsOnClick(target);
    let clicked = 0;
    target.addEventListener("click", () => { clicked++; });
    expect(await restoreBossListFilter("新招呼")).toBe(true);
    expect(clicked).toBe(1);
    expect(target.getAttribute("aria-selected")).toBe("true");
  });

  it("retries once when the first click did not take", async () => {
    const target = row("tab-new", "新招呼");
    row("tab-com", "沟通中", true);
    let clicked = 0;
    target.addEventListener("click", () => { clicked++; });
    // The tab never marks itself selected, so the restore must not claim success.
    expect(await restoreBossListFilter("新招呼")).toBe(false);
    expect(clicked).toBe(2);
  });

  it("returns to 新招呼 when the original list could not be identified", async () => {
    // The recruiter's tab could not be read, and the pass left the list on
    // “沟通中”: a pass that ends there is a pass that froze their working list.
    const target = row("tab-new", "新招呼");
    row("tab-com", "沟通中", true);
    selectsOnClick(target);
    expect(await restoreBossListFilter(undefined)).toBe(true);
    expect(target.getAttribute("aria-selected")).toBe("true");
  });

  it("does nothing when there is no filter to restore", async () => {
    const target = row("tab-com", "沟通中", true);
    let clicked = 0;
    target.addEventListener("click", () => { clicked++; });
    // The traversal's own list needs nothing, and a missing tab must not throw.
    expect(await restoreBossListFilter("沟通中")).toBe(false);
    expect(await restoreBossListFilter("新招呼")).toBe(false);
    expect(clicked).toBe(0);
  });
});

describe("what a polling pass may touch", () => {
  const ENTRY = {
    candidate_display_name: "候选人",
    job_display_name: "AI应用开发工程师",
    conversation_updated_at: "2026-09-10T10:00:00.000Z",
    synced: true,
  };

  it("never opens an unread row", () => {
    // Unread rows are the recruiter's inbox. Opening one is what BOSS counts
    // as reading it, so a pass must leave them entirely alone.
    const decision = decideBossRow(
      "1 09:35 候选人 AI应用开发工程师",
      "2026-09-14T09:35:00.000Z",
      { ...ENTRY, conversation_updated_at: "2026-09-01T00:00:00.000Z" },
    );
    expect(decision.open).toBe(false);
    expect(decision.skip).toBe("UNREAD");
  });

  it("opens a read row that has not reached the table yet", () => {
    const decision = decideBossRow(
      "09:35 候选人 AI应用开发工程师",
      "2026-09-14T09:35:00.000Z",
      undefined,
      true,
    );
    expect(decision.open).toBe(true);
    expect(decision.reason).toBe("HISTORY_SNAPSHOT");
  });

  it("opens a read row that has not reached Feishu yet", () => {
    const decision = decideBossRow(
      "09:35 候选人 AI应用开发工程师",
      "2026-09-14T09:35:00.000Z",
      { ...ENTRY, synced: false },
      true,
    );
    expect(decision.open).toBe(true);
    expect(decision.reason).toBe("HISTORY_SNAPSHOT");
  });

  it("refreshes a read row that is already in the table and moved", () => {
    const decision = decideBossRow(
      "09:35 候选人 AI应用开发工程师",
      "2026-09-14T09:35:00.000Z",
      ENTRY,
    );
    expect(decision.open).toBe(true);
    expect(decision.reason).toBe("CATCHUP_RECONCILED");
  });

  it("opens an unchanged read row so its snapshot is refreshed", () => {
    const decision = decideBossRow(
      "10:00 候选人 AI应用开发工程师",
      "2026-09-10T10:00:00.000Z",
      ENTRY,
      true,
    );
    expect(decision.open).toBe(true);
    expect(decision.reason).toBe("HISTORY_SNAPSHOT");
  });
});

describe("BOSS list rows read without opening a conversation", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  function mountList(rows: string[]) {
    document.body.innerHTML = `<section>${rows.map((text) => `<div>${text}</div>`).join("")}</section>`;
    for (const item of document.querySelectorAll<HTMLElement>("section > div")) {
      Object.defineProperty(item, "innerText", { value: item.textContent, configurable: true });
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

  it("splits every activity prefix into a candidate and a job", () => {
    expect(parseBossRowIdentity("09:35 乙候选人 AI应用开发工程师")).toEqual({
      candidateDisplayName: "乙候选人",
      jobDisplayName: "AI应用开发工程师",
    });
    expect(parseBossRowIdentity("1 昨天 朱亮 ai应用开发工程师")).toEqual({
      candidateDisplayName: "朱亮",
      jobDisplayName: "ai应用开发工程师",
    });
    expect(parseBossRowIdentity("昨天 23:51 旷野途 ai应用开发工程师")).toEqual({
      candidateDisplayName: "旷野途",
      jobDisplayName: "ai应用开发工程师",
    });
    expect(parseBossRowIdentity("09月05日 顾嘉雯 ai应用开发工程师")).toEqual({
      candidateDisplayName: "顾嘉雯",
      jobDisplayName: "ai应用开发工程师",
    });
  });

  it("keeps one identity per person and job across re-rendered whitespace", () => {
    expect(bossRowIdentity("09:35  乙候选人   AI应用开发工程师")).toBe(
      bossRowIdentity("09:41 乙候选人 AI应用开发工程师"),
    );
    expect(bossRowIdentity("09:35 乙候选人 AI应用开发工程师")).not.toBe(
      bossRowIdentity("09:35 乙候选人 数据分析师"),
    );
  });

  it("reads mounted rows with their activity and unread state, clicking nothing", () => {
    mountList(["1 09:35 乙候选人 AI应用开发工程师", "09:12 丙候选人 数据分析师"]);
    const clicks = vi.fn();
    document.querySelector("section")!.addEventListener("click", clicks);
    const rows = readBossMountedRows(new Date("2026-09-15T09:40:00+08:00"));
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ hasUnread: true });
    expect(rows[0].rowText).toContain("乙候选人");
    expect(rows[0].activity).toBe(parseBossListActivity(rows[0].rowText, new Date("2026-09-15T09:40:00+08:00"))!.toISOString());
    expect(rows[1]).toMatchObject({ hasUnread: false });
    expect(clicks).not.toHaveBeenCalled();
  });

  it("returns nothing when the list is not mounted", () => {
    document.body.innerHTML = "<div id='chat'>没有列表</div>";
    expect(readBossMountedRows()).toEqual([]);
  });

  it("never scrolls the list it reads, even when the list can scroll", () => {
    document.body.innerHTML =
      "<section><div id='viewport'><div>09:35 乙候选人 AI应用开发工程师</div><div>09:12 丙候选人 数据分析师</div></div></section>";
    const viewport = document.querySelector<HTMLElement>("#viewport")!;
    // A real, scrollable viewport: a traversal would move it, a read must not.
    Object.defineProperty(viewport, "scrollHeight", { value: 1200, configurable: true });
    Object.defineProperty(viewport, "clientHeight", { value: 300, configurable: true });
    let scrollWrites = 0;
    Object.defineProperty(viewport, "scrollTop", {
      get: () => 0,
      set: () => { scrollWrites += 1; },
      configurable: true,
    });
    for (const item of document.querySelectorAll<HTMLElement>("#viewport > div")) {
      Object.defineProperty(item, "innerText", { value: item.textContent, configurable: true });
      item.getBoundingClientRect = () => ({
        x: 0, y: 0, top: 0, left: 0, right: 280, bottom: 60,
        width: 280, height: 60, toJSON: () => ({}),
      });
    }
    const scrolled = vi.fn();
    viewport.addEventListener("scroll", scrolled);
    const clicks = vi.fn();
    document.querySelector("section")!.addEventListener("click", clicks);

    expect(readBossMountedRows(new Date("2026-09-15T09:40:00+08:00"))).toHaveLength(2);
    expect(scrollWrites).toBe(0);
    expect(scrolled).not.toHaveBeenCalled();
    expect(clicks).not.toHaveBeenCalled();
  });
});
