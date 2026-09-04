import { afterEach, describe, expect, it, vi } from "vitest";
import {
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
    expect(parseBossListActivity("候选人 09:35", now)?.getTime()).toBe(
      now.getTime(),
    );
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
});
