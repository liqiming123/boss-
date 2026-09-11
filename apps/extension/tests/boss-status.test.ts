import { describe, expect, it } from "vitest";
import {
  BOSS_DIALOG_CONFIRM,
  BOSS_INTERVIEW_DIALOG,
  BOSS_INTERVIEW_ENTRY,
  bossInterviewInviteEvidence,
  classifyBossOutgoingMessage,
  classifyBossStatus,
  hasBossRecruiterOutbound,
  isBossInviteScreenshotTrigger,
} from "../src/adapters/boss/boss-status";
describe("BOSS status rules", () => {
  it("keeps interview intent separate from a scheduled interview", () => {
    expect(classifyBossStatus("候选人说：我明天可以面试").status).toBe(
      "待约面",
    );
    expect(classifyBossStatus("面试邀请已发送").status).toBe("已约面");
  });
  it("uses the latest strong evidence in conversation order", () => {
    expect(
      classifyBossStatus("面试邀请已发送 后来：不好意思，不太合适哦 送达").status,
    ).toBe("已拒绝");
  });
  it("does not classify a rejection template without sent evidence", () => {
    expect(classifyBossStatus("不好意思，不太合适哦").status).toBe("沟通中");
    expect(
      classifyBossStatus("不好意思，不太合适哦 送达").status,
    ).toBe("已拒绝");
  });
  it("classifies explicit recruiter rejection text after confirmed send", () => {
    for (const text of [
      "你的经历与岗位不匹配，这次先不推进了",
      "很遗憾暂时无法继续推进，祝你求职顺利",
      "目前岗位已经招满，暂不考虑",
      "本轮面试不通过",
    ]) {
      expect(classifyBossOutgoingMessage(text).status).toBe("已拒绝");
    }
    expect(classifyBossOutgoingMessage("您好，方便明天下午面试吗").status).toBe(
      "待约面",
    );
  });
  it("recognizes the mobile rejection template used on the BOSS chat page", () => {
    expect(
      classifyBossStatus(
        "15:33 送达 对不起，看了你的简历以后觉得不太合适，希望你早日找到满意的工作机会",
        new Date().toISOString(),
        false,
      ).status,
    ).toBe("已拒绝");
    expect(
      classifyBossOutgoingMessage(
        "对不起，看了你的简历以后觉得不太合适，希望你早日找到满意的工作机会",
      ).status,
    ).toBe("已拒绝");
  });
  it("requires outbound evidence for catch-up creation", () => {
    expect(hasBossRecruiterOutbound("您好，我想应聘")).toBe(false);
    // Request markers can come from unrelated left-list snippets; only the
    // active conversation's delivery marker is safe for passive scanning.
    expect(hasBossRecruiterOutbound("微信交换请求已发送")).toBe(false);
    expect(
      hasBossRecruiterOutbound(
        "左侧：不好意思，不太合适哦 沟通职位：短视频编导 候选人新消息",
      ),
    ).toBe(false);
  });
  it("only asks for the intrusive chat capture when an invitation is handed out", () => {
    expect(isBossInviteScreenshotTrigger("面试邀请已发送")).toBe(true);
    expect(
      isBossInviteScreenshotTrigger("已约面试，明天下午两点", undefined),
    ).toBe(true);
    expect(
      isBossInviteScreenshotTrigger(undefined, {
        status: "已约面",
        evidence: "BOSS_INTERVIEW_MARKER",
        ruleVersion: "boss-status-v4",
        observedAt: new Date().toISOString(),
      }),
    ).toBe(true);
    // A question is intent, not the stage change we must document.
    expect(isBossInviteScreenshotTrigger("您好，方便明天下午面试吗")).toBe(
      false,
    );
    expect(isBossInviteScreenshotTrigger("您好，想和您聊聊岗位")).toBe(false);
    expect(isBossInviteScreenshotTrigger("简历请求已发送")).toBe(false);
    expect(isBossInviteScreenshotTrigger("不好意思，不太合适哦 送达")).toBe(
      false,
    );
    expect(isBossInviteScreenshotTrigger(undefined, undefined)).toBe(false);
  });
  it("treats BOSS's invitation confirmation variants as a scheduled interview", () => {
    for (const marker of [
      "发送了面试邀请",
      "面试邀请已发送",
      "面试邀约已发送",
      "已发送面试邀请",
      "邀请已发送",
    ]) {
      expect(classifyBossStatus(marker).status).toBe("已约面");
      expect(hasBossRecruiterOutbound(`沟通职位：短视频编导 ${marker}`)).toBe(
        true,
      );
    }
    // The scheduling entry control is an action, never a chat message.
    expect(classifyBossOutgoingMessage("约面试").status).toBe("沟通中");
  });
  it("recognizes the interview entry and dialog confirmation controls", () => {
    for (const label of ["约面", "约面试", "预约面试", "发起面试", "发面试邀请"]) {
      expect(BOSS_INTERVIEW_ENTRY.test(label)).toBe(true);
    }
    expect(BOSS_INTERVIEW_ENTRY.test("发送")).toBe(false);
    expect(BOSS_INTERVIEW_ENTRY.test("不合适")).toBe(false);
    for (const label of ["发送", "发送邀请", "确认", "确定", "提交"]) {
      expect(BOSS_DIALOG_CONFIRM.test(label)).toBe(true);
    }
    expect(BOSS_DIALOG_CONFIRM.test("约面试")).toBe(false);
    // The scheduler dialog carries these fields; the sent bubble does not.
    expect(BOSS_INTERVIEW_DIALOG.test("面试时间：选择日期 选择开始时间")).toBe(
      true,
    );
    expect(BOSS_INTERVIEW_DIALOG.test("发送了面试邀请")).toBe(false);
  });
  it("classifies BOSS's real sent-invitation bubble as a scheduled interview", () => {
    expect(classifyBossStatus("15:29 发送了面试邀请").status).toBe("已约面");
    expect(
      classifyBossStatus("发送了面试邀请", new Date().toISOString(), false)
        .status,
    ).toBe("已约面");
    expect(hasBossRecruiterOutbound("沟通职位：业务助理 发送了面试邀请")).toBe(
      true,
    );
  });
  it("requests the chat capture for a dialog-sent invitation with no draft", () => {
    const evidence = bossInterviewInviteEvidence();
    expect(evidence).toMatchObject({
      status: "已约面",
      evidence: "BOSS_INTERVIEW_INVITE",
    });
    expect(isBossInviteScreenshotTrigger(undefined, evidence)).toBe(true);
  });
});
