import type { StatusEvidence } from "../types";
import { normalizeBossText } from "./boss-normalizers";

export const BOSS_STATUS_RULE_VERSION = "boss-status-v6";

/** BOSS's own confirmation that an interview invitation reached the candidate.
 * The current desktop build renders the system bubble 「发送了面试邀请」 in the
 * conversation; older wording variants are kept so both stay recognized. */
export const BOSS_INVITE_SENT_MARKER =
  /(?:发送了面试邀请|面试邀请已发送|面试邀约已发送|已发送面试邀请|邀请已发送)/;

/** The conversation-region control that starts BOSS's interview scheduling
 * flow. It is an action (“约面”), never a chat draft: treating the button text
 * as an outgoing message produced a premature 沟通中 record and swallowed the
 * real invitation. */
export const BOSS_INTERVIEW_ENTRY =
  /(?:约面|约面试|预约面试|约个面|发起面试|安排面试|邀请面试|发面试邀请|面试邀约)/;

/** Confirmation controls inside the interview scheduling dialog. */
export const BOSS_DIALOG_CONFIRM =
  /^(?:发送|发送邀请|立即发送|确认发送|确认|确定|提交|完成)$/;

/** Evidence that the interview scheduler (not the chat composer) is open.
 * Used to scope a confirmation click that is rendered outside the chat pane. */
export const BOSS_INTERVIEW_DIALOG =
  /(?:面试时间|面试地址|面试地点|面试官|面试方式|面试时长|面试轮次|选择时间)/;

const INVITE_STATUS = "已约面";

/** Stage-change evidence for a confirmed interview invitation. It is a
 * first-class signal so a dialog send with no chat draft still advances the
 * candidate to 已约面 and requests the historical chat capture. */
export function bossInterviewInviteEvidence(
  observedAt = new Date().toISOString(),
): StatusEvidence {
  return {
    status: INVITE_STATUS,
    evidence: "BOSS_INTERVIEW_INVITE",
    ruleVersion: BOSS_STATUS_RULE_VERSION,
    observedAt,
  };
}

const EXPLICIT_REJECTION =
  /(?:(?:不太?合适|不合适|暂不合适|不匹配|不符合(?:岗位|职位|要求|需求)?|暂(?:时)?不考虑|不再考虑|不予考虑|不通过|无法(?:继续)?推进|不再推进|先不推进|暂停推进|婉拒|岗位(?:已经?)?招满|很遗憾.{0,18}(?:不能|无法|不予))|(?:对不起|抱歉).{0,80}(?:不太?合适|不合适|不匹配|不考虑))/;

const DELIVERED_REJECTION = new RegExp(
  `(?:${EXPLICIT_REJECTION.source}.{0,50}送达|送达.{0,50}${EXPLICIT_REJECTION.source})`,
);

const RULES: Array<{ status: string; pattern: RegExp; evidence: string }> = [
  {
    status: "已入职",
    pattern: /(?:已录用|录用成功|已入职)(?!人数)/,
    evidence: "BOSS_HIRED_MARKER",
  },
  {
    status: "已拒绝",
    pattern: new RegExp(`(?:已拒绝该候选人|${DELIVERED_REJECTION.source})`),
    evidence: "EXPLICIT_REJECTION",
  },
  {
    status: INVITE_STATUS,
    pattern: new RegExp(
      `(?:${BOSS_INVITE_SENT_MARKER.source}|面试时间[：:]|面试已安排|已约面试)`,
    ),
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
    ? [...RULES, { status: "待约面", pattern: /(?:可以面试|方便.{0,12}面试|想约.{0,8}面试|安排.{0,8}面试)/, evidence: "INTERVIEW_INTENT" }]
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

/** Classify text captured from the recruiter editor after BOSS confirms send.
 * Unlike page-wide scanning this text is known to be outbound, so explicit
 * intent does not need an adjacent delivery marker and cannot come from the
 * candidate's messages or profile. */
export function classifyBossOutgoingMessage(
  text: string,
  observedAt = new Date().toISOString(),
): StatusEvidence {
  const normalized = normalizeBossText(text);
  if (EXPLICIT_REJECTION.test(normalized))
    return {
      status: "已拒绝",
      evidence: "EXPLICIT_REJECTION",
      ruleVersion: BOSS_STATUS_RULE_VERSION,
      observedAt,
    };
  if (/(?:继续|重新|再).{0,12}(?:聊|沟通|联系|了解)|(?:前几天|之前|上次).{0,20}(?:聊过|沟通过)/.test(normalized)) {
    const intent = classifyBossStatus(normalized, observedAt, true);
    return { ...intent, evidence: "RECRUITER_RECONTACT_INTENT" };
  }
  return classifyBossStatus(normalized, observedAt, true);
}

/** Only an explicit interview scheduling action justifies the intrusive
 * historical chat capture. Opening a conversation, chatting, or merely
 * expressing interview intent must never hijack the recruiter's scroll
 * position. */
const INVITE_EVIDENCE = new Set(["BOSS_INTERVIEW_MARKER", "BOSS_INTERVIEW_INVITE"]);

/** True when the outgoing message hands the candidate an interview invitation.
 * A question such as “方便面试吗” is only intent (待约面) and is deliberately
 * excluded: the state change we must document is the actual invitation. */
export function isBossInviteScreenshotTrigger(
  messageText: string | undefined,
  statusEvidence?: StatusEvidence,
): boolean {
  if (statusEvidence) {
    if (statusEvidence.status === INVITE_STATUS) return true;
    if (INVITE_EVIDENCE.has(statusEvidence.evidence)) return true;
  }
  return !!messageText && classifyBossOutgoingMessage(messageText).status === INVITE_STATUS;
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
  return new RegExp(
    `送达|简历请求已发送|电话(?:交换)?请求已发送|微信(?:交换)?请求已发送|${BOSS_INVITE_SENT_MARKER.source}`,
  ).test(active);
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
