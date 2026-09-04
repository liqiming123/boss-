import { describe, expect, it } from "vitest";
import { parseBossConversationTimes } from "../src/adapters/boss/boss-times";

const now = new Date("2026-09-02T06:00:00.000Z");

describe("BOSS conversation times", () => {
  it("extracts the first system conversation time and latest message time", () => {
    const text =
      "列表候选人\n王照亭\n23岁\n1年\n本科\n昨天 17:25\n9月1日 沟通的职位-ai应用开发工程师\n今天 13:43\n您好，期待回复";
    expect(parseBossConversationTimes(text, "王照亭", now)).toEqual({
      conversationStartedAt: "2026-09-01T09:25:00.000Z",
      conversationUpdatedAt: "2026-09-02T05:43:00.000Z",
    });
  });
  it("infers the previous year for a future-looking yearless date", () => {
    const text =
      "张三\n25岁\n本科\n12月31日 23:50\n12月31日 沟通的职位-测试岗位";
    expect(
      parseBossConversationTimes(
        text,
        "张三",
        new Date("2027-01-01T00:10:00.000Z"),
      ).conversationStartedAt,
    ).toBe("2026-12-31T15:50:00.000Z");
  });
  it("leaves unverified date-only values empty", () => {
    const text = "张三\n25岁\n本科\n9月1日 沟通的职位-测试岗位";
    expect(parseBossConversationTimes(text, "张三", now)).toEqual({});
  });
  it("supports a full date and time", () => {
    const text =
      "张三\n25岁\n本科\n2026-08-30 09:15\n2026-08-30 09:15 沟通的职位-测试岗位\n2026-09-01 18:20 最新回复";
    expect(parseBossConversationTimes(text, "张三", now)).toEqual({
      conversationStartedAt: "2026-08-30T01:15:00.000Z",
      conversationUpdatedAt: "2026-09-01T10:20:00.000Z",
    });
  });
  it("supports the numeric month-day time used by the current chat page", () => {
    const text =
      "振理\n29岁\n6年\n硕士\n08-05 18:06\n08-05 18:06 沟通的职位-业务助理";
    expect(parseBossConversationTimes(text, "振理", now)).toEqual({
      conversationStartedAt: "2026-08-05T10:06:00.000Z",
      conversationUpdatedAt: "2026-08-05T10:06:00.000Z",
    });
  });
});
