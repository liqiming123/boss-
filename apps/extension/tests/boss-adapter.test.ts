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
