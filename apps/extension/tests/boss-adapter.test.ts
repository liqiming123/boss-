import { describe, expect, it, beforeEach } from "vitest";
import { BossAdapter } from "../src/adapters/boss/boss-adapter";
describe("Boss adapter", () => {
  beforeEach(() => {
    history.replaceState({}, "", "/web/chat/index");
    document.body.innerHTML = "";
  });
  it("fails closed outside a verified chat page", async () => {
    history.replaceState({}, "", "/web/chat/job/list");
    const result = await new BossAdapter().extractCandidate();
    expect(result.status).toBe("ERROR");
    expect((result as { errorCode: string }).errorCode).toBe(
      "BOSS_UNSUPPORTED_PAGE",
    );
  });
  it("accepts only exact BOSS domains and their subdomains", () => {
    const adapter = new BossAdapter();
    expect(adapter.canHandle("https://www.zhipin.com/web/chat/index")).toBe(
      true,
    );
    expect(adapter.canHandle("https://evilzhipin.com/web/chat/index")).toBe(
      false,
    );
  });
  it("extracts the account on communication list variants used for background scans", async () => {
    history.replaceState({}, "", "/web/chat/interaction");
    document.body.innerText = "职位管理\n账号权益\n李先生\n互动\n候选人列表";
    expect(await new BossAdapter().extractAccount()).toEqual({
      status: "OK",
      value: { displayName: "李先生" },
    });
    expect(new BossAdapter().isCandidateConversationPage()).toBe(false);
    document.body.innerText =
      "职位管理\n账号权益\n李先生\n互动\n黄海南\n23岁\n沟通职位：业务助理";
    expect(new BossAdapter().isCandidateConversationPage()).toBe(true);
  });
  it("extracts the split-line candidate layout used by the chat detail panel", async () => {
    document.body.innerText =
      "职位管理\n升级VIP\n李先生\n沟通\n黄海南\n刚刚活跃\n28岁\n6年\n本科\n沟通职位：\nai应用开发工程师";
    const adapter = new BossAdapter();
    expect(await adapter.extractCandidate()).toMatchObject({
      status: "OK",
      value: {
        displayName: "黄海南",
        age: 28,
        experience: "6年",
        education: "本科",
        hasRecruiterOutbound: false,
      },
    });
    expect(await adapter.extractAccount()).toEqual({
      status: "OK",
      value: { displayName: "李先生" },
    });
    expect(await adapter.extractJob()).toEqual({
      status: "OK",
      value: { displayName: "ai应用开发工程师" },
    });
  });
  it("accepts compact and punctuation variants without depending on one exact DOM shape", async () => {
    document.body.innerText =
      "招聘数据 | 王女士 | 沟通的职位-ai应用开发工程师\n赵明 27岁 硕士";
    const adapter = new BossAdapter();
    expect((await adapter.extractCandidate()).status).toBe("OK");
    expect(await adapter.extractJob()).toEqual({
      status: "OK",
      value: { displayName: "ai应用开发工程师" },
    });
  });
  it("extracts a bare recruiter name shown after upgrade VIP", async () => {
    document.body.innerText =
      "招聘数据\n账号权益\n升级VIP\n成珈莉\n振理\n29岁\n6年\n硕士\n沟通职位：业务助理";
    expect(await new BossAdapter().extractAccount()).toEqual({
      status: "OK",
      value: { displayName: "成珈莉" },
    });
  });
  it("recognises nickname-style account names carrying letters or digits", async () => {
    // BOSS-side nicknames are not real names: the account rules must not veto
    // them the way the candidate rules do, or a fresh install can never bind.
    history.replaceState({}, "", "/web/chat/interaction");
    document.body.innerText = "职位管理\n账号权益\nAmy王\n互动";
    expect(await new BossAdapter().extractAccount()).toEqual({
      status: "OK",
      value: { displayName: "Amy王" },
    });
    document.body.innerText = "职位管理\n账号权益\n招聘01\n互动";
    expect(await new BossAdapter().extractAccount()).toEqual({
      status: "OK",
      value: { displayName: "招聘01" },
    });
  });
  it("keeps a chat line after the account chip from becoming the account name", async () => {
    history.replaceState({}, "", "/web/chat/interaction");
    document.body.innerText = "职位管理\n账号权益\n有剧本吗\n互动";
    expect(await new BossAdapter().extractAccount()).toEqual({
      status: "ERROR",
      errorCode: "BOSS_FIELDS_NOT_FOUND",
    });
  });
  it("accepts candidate display names ending in 女士", async () => {
    document.body.innerText =
      "升级VIP\n成珈莉\n贾女士\n28岁\n3年\n本科\n沟通职位：业务助理";
    expect(await new BossAdapter().extractCandidate()).toMatchObject({
      status: "OK",
      value: { displayName: "贾女士", age: 28 },
    });
  });
  it("normalizes spaces inserted between candidate name characters", async () => {
    document.body.innerText =
      "升级VIP\n李先生\n王 睿\n22岁\n2年\n本科\n沟通职位：ai应用开发工程师";
    expect(await new BossAdapter().extractCandidate()).toMatchObject({
      status: "OK",
      value: { displayName: "王睿", age: 22 },
    });
  });
  it("keeps profile extraction working when age is omitted and accepts expanded experience formats", async () => {
    document.body.innerText =
      "升级VIP\n成珈莉\n顾嘉雯\n刚刚活跃\n工作经验：1-3年\n本科\n沟通职位：业务助理/总经理助理";
    expect(await new BossAdapter().extractCandidate()).toMatchObject({
      status: "OK",
      value: { displayName: "顾嘉雯", experience: "1-3年", education: "本科" },
    });
  });
  it.each([
    "未填写工作经历",
    "有剧本吗",
    "测试时间有限制吗",
    "嗯嗯好的感谢",
  ])("never treats the chat text %s as the candidate name", async (chatText) => {
    document.body.innerText =
      `升级VIP\n成珈莉\n真实姓名\n${chatText}\n7年\n大专\n沟通职位：AI生成师(抽卡师)`;
    expect(await new BossAdapter().extractCandidate()).toMatchObject({
      status: "OK",
      value: { displayName: "真实姓名", experience: "7年", education: "大专" },
    });
  });
  it("preserves graduation cohort instead of reading it as years of experience", async () => {
    document.body.innerText =
      "升级VIP\n成珈莉\n陈明俊\n22岁\n26届\n本科\n沟通职位：ai应用开发工程师\n我是26年毕业生";
    expect(await new BossAdapter().extractCandidate()).toMatchObject({
      status: "OK",
      value: {
        displayName: "陈明俊",
        age: 22,
        experience: "26届",
        education: "本科",
      },
    });
  });
  it("does not read a graduation year as work experience", async () => {
    document.body.innerText =
      "升级VIP\n成珈莉\n朱雨晴\n刚刚活跃\n24岁\n26年毕业\n硕士\n沟通职位：海外社媒运营";
    expect(await new BossAdapter().extractCandidate()).toMatchObject({
      status: "OK",
      value: {
        displayName: "朱雨晴",
        age: 24,
        experience: "26届",
        education: "硕士",
      },
    });
    document.body.innerText =
      "升级VIP\n成珈莉\n加油\n20岁\n2027年毕业生\n大专\n沟通职位：美妆博主(店播)";
    expect(await new BossAdapter().extractCandidate()).toMatchObject({
      status: "OK",
      value: {
        displayName: "加油",
        age: 20,
        experience: "27届",
        education: "大专",
      },
    });
    document.body.innerText =
      "升级VIP\n成珈莉\n小金\n24岁\n26年应届\n本科\n沟通职位：业务助理/总经理助理";
    expect(await new BossAdapter().extractCandidate()).toMatchObject({
      status: "OK",
      value: {
        displayName: "小金",
        age: 24,
        experience: "26届",
        education: "本科",
      },
    });
  });
  it("prefers real experience over a graduation cohort on the same profile", async () => {
    document.body.innerText =
      "升级VIP\n成珈莉\n谢玉媛\n33岁\n10年\n本科\n26届\n沟通职位：财务主管";
    expect(await new BossAdapter().extractCandidate()).toMatchObject({
      status: "OK",
      value: { displayName: "谢玉媛", age: 33, experience: "10年" },
    });
  });
  it("never reads a calendar date as work experience", async () => {
    document.body.innerText =
      "升级VIP\n成珈莉\n刘雅丽\n30岁\n2026年09月09日 12:32\n中专\n沟通职位：美妆博主(店播)";
    const result = await new BossAdapter().extractCandidate();
    if (result.status !== "OK")
      throw new Error("expected a candidate extraction");
    expect(result.value).toMatchObject({ displayName: "刘雅丽", age: 30 });
    expect(result.value.experience).toBeUndefined();
  });
  it("rejects the conversation-list load-more control as a candidate", async () => {
    document.body.innerText =
      "职位管理\n升级VIP\n李先生\n沟通\n滚动加载更多\n26岁\n7年\n本科\n沟通职位：美妆博主(店播)";
    const result = await new BossAdapter().extractCandidate();
    expect(result.status).toBe("ERROR");
  });
  it("trims the candidate profile tail from the job label", async () => {
    document.body.innerText =
      "升级VIP\n成珈莉\n谢玉媛\n33岁\n10年\n本科\n沟通职位：财务主管 最近关注：无锡";
    expect(await new BossAdapter().extractJob()).toEqual({
      status: "OK",
      value: { displayName: "财务主管" },
    });
  });
  it("cuts the chat text BOSS renders on the job label line", async () => {
    document.body.innerText =
      "升级VIP\n成珈莉\n谢玉媛\n33岁\n10年\n本科\n沟通职位：财务主管 最近关注: 无锡 · 财务经理/主管 8-12K 昨天 16:39 已读 你好,经理这边说可以安排面试,我先给你说下公司情况";
    expect(await new BossAdapter().extractJob()).toEqual({
      status: "OK",
      value: { displayName: "财务主管" },
    });
    document.body.innerText =
      "升级VIP\n成珈莉\n某候选人\n25岁\n2年\n本科\n沟通职位：直播助播 您好,我想和您沟通下这个职位的细节,期待您的回复 干过有两年经验会投流选品";
    expect(await new BossAdapter().extractJob()).toEqual({
      status: "OK",
      value: { displayName: "直播助播" },
    });
    // The real BOSS line prefixes the greeting with the account name; the
    // leftover used to survive into the identity key and create one row per
    // message for the same candidate.
    document.body.innerText =
      "升级VIP\n成珈莉\n张雨庭\n19岁\n27届\n大专\n沟通职位：AI短视频内容生成师 BOSS您好,我具备岗位所需技能,且学习能力强,可以给您发简历看看吗? 10:33 已读 这个是AI相关的岗位";
    expect(await new BossAdapter().extractJob()).toEqual({
      status: "OK",
      value: { displayName: "AI短视频内容生成师" },
    });
  });
  it("keeps legitimate compound job titles intact", async () => {
    for (const title of [
      "业务助理/总经理助理",
      "ai应用开发工程师",
      "美妆博主(店播)",
      "AI生成师(抽卡师)",
    ]) {
      document.body.innerText = `升级VIP\n成珈莉\n某候选人\n25岁\n2年\n本科\n沟通职位：${title}`;
      expect(await new BossAdapter().extractJob()).toEqual({
        status: "OK",
        value: { displayName: title },
      });
    }
  });
  it("does not treat an unsent recruiter quick-action template as outbound", async () => {
    document.body.innerText =
      "王睿\n22岁\n本科\n沟通职位：ai应用开发工程师\n你好啊，可以聊一聊~";
    expect(await new BossAdapter().extractCandidate()).toMatchObject({
      status: "OK",
      value: { hasRecruiterOutbound: false },
    });
  });
  it("recognizes a delivered recruiter bubble in the active panel", async () => {
    document.body.innerText =
      "王睿\n22岁\n本科\n沟通职位：ai应用开发工程师\n你好啊，可以聊一聊~\n送达";
    expect(await new BossAdapter().extractCandidate()).toMatchObject({
      status: "OK",
      value: { hasRecruiterOutbound: true },
    });
  });
});
