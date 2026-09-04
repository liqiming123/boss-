import { describe, expect, it } from "vitest";
import { isBossHostname } from "../src/adapters/boss/boss-hosts";

describe("resume preview upload guards", () => {
  it("accepts only BOSS hostnames for attachment downloads", () => {
    expect(isBossHostname("www.zhipin.com")).toBe(true);
    expect(isBossHostname("cdn.bosszhipin.com")).toBe(true);
    expect(isBossHostname("evilzhipin.com")).toBe(false);
  });
});
