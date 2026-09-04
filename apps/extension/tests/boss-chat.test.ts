import { afterEach, describe, expect, it } from "vitest";
import {
  hasConversationResumeAttachment,
  summarizeBossConversation,
} from "../src/adapters/boss/boss-chat";

describe("BOSS structured chat summary", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("accepts a resume attachment card as conversation evidence", () => {
    expect(
      hasConversationResumeAttachment(
        "沟通职位：AI应用开发工程师 点击预览附件简历 简历-候选人.pdf",
      ),
    ).toBe(true);
    expect(hasConversationResumeAttachment("附件简历 在线简历")).toBe(false);
    expect(
      summarizeBossConversation(
        "沟通职位：AI应用开发工程师 点击预览附件简历 简历-候选人.pdf",
      ).hasRecruiterOutbound,
    ).toBe(true);
  });

  it("ignores list snippets and unsent quick actions before the active conversation", () => {
    const summary = summarizeBossConversation(
      "左侧候选人：不好意思，不太合适哦 送达\n沟通职位：短视频编导\n求简历\n候选人您好",
    );
    expect(summary.hasRecruiterOutbound).toBe(false);
    expect(summary.actions).toEqual([]);
  });

  it("returns only whitelisted metadata for confirmed recruiter activity", () => {
    const summary = summarizeBossConversation(
      "沟通职位：短视频编导\n简历请求已发送\n不好意思，不太合适哦 送达",
      "2026-09-03T08:00:00.000Z",
    );
    expect(summary).toMatchObject({
      status: "CONFIRMED",
      hasRecruiterOutbound: true,
      recruiterMessageCount: 2,
      lastRecruiterMessageAt: "2026-09-03T08:00:00.000Z",
      actions: ["RESUME_REQUEST", "REJECTION"],
    });
    expect(summary).not.toHaveProperty("messages");
  });
});
