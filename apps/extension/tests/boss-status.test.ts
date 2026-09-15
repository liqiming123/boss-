import { describe, expect, it } from "vitest";
import {
  BOSS_DIALOG_CONFIRM,
  BOSS_INTERVIEW_DIALOG,
  BOSS_INTERVIEW_ENTRY,
  bossInterviewInviteEvidence,
  classifyBossOutgoingMessage,
  classifyBossStatus,
  hasBossRecruiterOutbound,
  resolveBossStatusEvidence,
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
  it("classifies a dialog-sent invitation with no draft", () => {
    const evidence = bossInterviewInviteEvidence();
    expect(evidence).toMatchObject({
      status: "已约面",
      evidence: "BOSS_INTERVIEW_INVITE",
    });
  });
  it("does not read page chrome as a rejection", () => {
    // BOSS renders “不合适” as an action button inside the conversation region.
    expect(classifyBossStatus("不合适").status).toBe("沟通中");
    expect(classifyBossOutgoingMessage("不合适").status).toBe("沟通中");
    expect(
      classifyBossStatus("求简历 换电话 换微信 查看面试 不合适").status,
    ).toBe("沟通中");
    // An invitation sent after the button is still an invitation.
    expect(
      classifyBossStatus("不合适 发送了面试邀请").status,
    ).toBe("已约面");
  });
  it("does not read recruiter courtesy as a rejection", () => {
    for (const text of [
      "同学你好，如果觉得不合适可以随时告诉我",
      "不合适的话也没关系，祝你顺利",
      "如不合适请点击不合适",
    ]) {
      expect(classifyBossOutgoingMessage(text).status).not.toBe("已拒绝");
    }
  });
  it("still recognizes every real rejection phrasing", () => {
    for (const text of [
      "你的经历与岗位不匹配，这次先不推进了",
      "目前岗位已经招满，暂不考虑",
      "本轮面试不通过",
      "不好意思，不太合适哦 送达",
      "对不起，看了你的简历以后觉得不太合适，希望你早日找到满意的工作机会",
    ]) {
      expect(classifyBossOutgoingMessage(text).status).toBe("已拒绝");
    }
  });
  it("lets a newer invitation outrank an older rejection", () => {
    // The old rejection-first rule let this combination stay 已拒绝 forever.
    expect(
      classifyBossStatus("不合适 后来 发送了面试邀请").status,
    ).toBe("已约面");
    // A genuine decline after the invitation still wins, because it is newer.
    expect(
      classifyBossStatus("发送了面试邀请 后来 拒接了面试邀请").status,
    ).toBe("已拒绝");
  });
  it("prefers the conversation's newest signal across both representations", () => {
    const invite = classifyBossStatus("发送了面试邀请");
    const rejection = classifyBossStatus("不好意思，不太合适哦 送达");
    expect(invite.status).toBe("已约面");
    expect(rejection.status).toBe("已拒绝");
    // Conversation text is newer than the bubbles we could classify: the
    // invitation the recruiter actually sent must not be locked out.
    expect(resolveBossStatusEvidence(invite, rejection).status).toBe("已约面");
    // A rejection in the conversation outranks an older outgoing invite.
    expect(resolveBossStatusEvidence(rejection, invite).status).toBe("已拒绝");
    // Neutral outgoing bubbles never override a real conversation signal.
    const neutral = classifyBossStatus("您好，想和您聊聊岗位");
    expect(neutral.status).toBe("沟通中");
    expect(resolveBossStatusEvidence(invite, neutral).status).toBe("已约面");
    // Nothing readable anywhere stays neutral rather than guessing.
    expect(resolveBossStatusEvidence(null, null).status).toBe("沟通中");
  });
});
