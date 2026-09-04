import { describe, expect, it } from "vitest";
import { parseBossNativeCommunicationHistory } from "../src/adapters/boss/boss-native-history";

describe("BOSS native communication history", () => {
  it("parses all colleague recruiters, jobs and timestamps from the verified modal text", () => {
    const records =
      parseBossNativeCommunicationHistory(`合作客户专享，了解同事沟通进度
同事沟通 我的沟通
Ta向 王文懋 发起沟通[总经理助理]
2026-08-05 18:27
Ta向 谢玲 发起沟通[业务助理/总经理助理]
2026-07-24 12:38
Ta向 焦梅 发起沟通[总经理助理]
2026-07-24 12:35`);
    expect(records).toEqual([
      {
        recruiterName: "王文懋",
        jobName: "总经理助理",
        contactedAt: "2026-08-05T18:27:00+08:00",
        source: "BOSS_NATIVE",
      },
      {
        recruiterName: "谢玲",
        jobName: "业务助理/总经理助理",
        contactedAt: "2026-07-24T12:38:00+08:00",
        source: "BOSS_NATIVE",
      },
      {
        recruiterName: "焦梅",
        jobName: "总经理助理",
        contactedAt: "2026-07-24T12:35:00+08:00",
        source: "BOSS_NATIVE",
      },
    ]);
  });
  it("fails closed when the text is not inside the native history surface", () => {
    expect(
      parseBossNativeCommunicationHistory(
        "聊天消息：Ta向王文懋发起沟通[总经理助理] 2026-08-05 18:27",
      ),
    ).toEqual([]);
  });
});
