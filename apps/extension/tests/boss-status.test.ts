import { describe, expect, it } from "vitest";
import {
  classifyBossStatus,
  hasBossRecruiterOutbound,
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
});
