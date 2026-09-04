import type { StatusEvidence } from "../types";
import { normalizeBossText } from "./boss-normalizers";

export const BOSS_STATUS_RULE_VERSION = "boss-status-v3";

const RULES: Array<{ status: string; pattern: RegExp; evidence: string }> = [
  {
    status: "已入职",
    pattern: /(?:已录用|录用成功|已入职)(?!人数)/,
    evidence: "BOSS_HIRED_MARKER",
  },
  {
    status: "已拒绝",
    pattern: /(?:已拒绝该候选人|不好意思[，, ]*不太合适哦.{0,30}送达|送达.{0,30}不好意思[，, ]*不太合适哦)/,
    evidence: "EXPLICIT_REJECTION",
  },
  {
    status: "已约面",
    pattern: /(?:面试邀请已发送|面试时间[：:]|面试已安排|已约面试)/,
    evidence: "BOSS_INTERVIEW_MARKER",
  },
  {
    status: "已交换联系方式",
    pattern: /(?:电话(?:交换)?请求已发送|微信(?:交换)?请求已发送)/,
    evidence: "BOSS_CONTACT_MARKER",
  },
  {
    status: "已获取简历",
    pattern: /(?:简历请求已发送|简历已接收|已获取到简历)/,
    evidence: "BOSS_RESUME_MARKER",
  },
];

export function classifyBossStatus(
  text: string,
  observedAt = new Date().toISOString(),
  includeInterviewIntent = true,
): StatusEvidence {
  const normalized = normalizeBossText(text);
  const rules = includeInterviewIntent
    ? [...RULES, { status: "待约面", pattern: /(?:可以面试|方便面试|想约.{0,8}面试|安排.{0,8}面试)/, evidence: "INTERVIEW_INTENT" }]
    : RULES;
  const rule = rules.map((item) => {
    const matches = [
      ...normalized.matchAll(
        new RegExp(
          item.pattern.source,
          `${item.pattern.flags.replace("g", "")}g`,
        ),
      ),
    ];
    return { item, index: matches.at(-1)?.index ?? -1 };
  })
    .filter((value) => value.index >= 0)
    .sort((a, b) => b.index - a.index)[0]?.item;
  return {
    status: rule?.status ?? "沟通中",
    evidence: rule?.evidence ?? "RECRUITER_OUTBOUND",
    ruleVersion: BOSS_STATUS_RULE_VERSION,
    observedAt,
  };
}

export function hasBossRecruiterOutbound(text: string): boolean {
  // The page-wide text also contains snippets from every candidate in the
  // left conversation list. Restrict passive history detection to the active
  // detail panel (the final “沟通职位” section), then accept BOSS's delivery
  // marker and its recruiter quick-action bubbles. This lets opening an
  // already-contacted conversation synchronize immediately, without treating
  // an unrelated list snippet as an outbound message.
  const normalized = normalizeBossText(text);
  const active = normalized.slice(normalized.lastIndexOf("沟通职位"));
  return /送达|简历请求已发送|电话(?:交换)?请求已发送|微信(?:交换)?请求已发送|面试邀请已发送/.test(active);
}

export function bossHistoricalJobs(text: string): string[] {
  const normalized = normalizeBossText(text);
  const values = new Set<string>();
  for (const match of normalized.matchAll(
    /沟通(?:的)?职位\s*[-—：:]\s*([^\n|]{1,100})/g,
  )) {
    const value = match[1].replace(/\s*(?:期望|薪资)[：:].*$/, "").trim();
    if (value) values.add(value);
  }
  return [...values];
}
