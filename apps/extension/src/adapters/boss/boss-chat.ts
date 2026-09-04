import { normalizeBossText } from "./boss-normalizers";

export const BOSS_CHAT_ACTIONS = [
  "RESUME_REQUEST",
  "CONTACT_EXCHANGE",
  "INTERVIEW_INVITE",
  "REJECTION",
  "HIRED",
] as const;

export type BossChatAction = (typeof BOSS_CHAT_ACTIONS)[number];

export type BossChatSummary = {
  status: "NONE" | "PARTIAL" | "CONFIRMED";
  evidenceText: string;
  hasRecruiterOutbound: boolean;
  recruiterMessageCount?: number;
  candidateMessageCount?: number;
  lastRecruiterMessageAt?: string;
  lastCandidateMessageAt?: string;
  actions: BossChatAction[];
};

const SENT_SYSTEM_ACTION =
  /(?:简历请求已发送|电话(?:交换)?请求已发送|微信(?:交换)?请求已发送|面试邀请已发送|邀请已发送)/g;
const OUTBOUND_DELIVERY = /送达/g;

/** BOSS may render a recruiter-requested resume as an attachment card without
 * rendering the older “简历请求已发送” system label. This is checked only in
 * the active conversation region by bossConversationEvidenceText(). */
export function hasConversationResumeAttachment(text: string): boolean {
  return /点击预览附件简历/.test(text) && /(?:\.pdf|简历)/i.test(text);
}

function visibleRect(element: HTMLElement) {
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0;
}

function interactive(element: HTMLElement) {
  return !!element.closest(
    'button,[role="button"],textarea,input,[contenteditable="true"]',
  );
}

/**
 * Locate the active right-hand conversation without relying on unverified BOSS
 * class names. Geometry and visible conversation evidence are deliberately
 * used instead; false negatives are safer than syncing an untouched person.
 */
export function findBossConversationRegion(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
  const candidates = [...document.querySelectorAll<HTMLElement>("main,section,div")]
    .filter((element) => !interactive(element))
    .filter((element) => {
      const rect = element.getBoundingClientRect();
      const text = normalizeBossText(element.innerText || element.textContent || "");
      return (
        visibleRect(element) &&
        rect.left > viewportWidth * 0.28 &&
        rect.width > 360 &&
        rect.height > 150 &&
        /沟通(?:的)?职位|送达|请求已发送|面试邀请已发送/.test(text) &&
        !/全部职位/.test(text)
      );
    })
    .sort((left, right) => {
      const area = (element: HTMLElement) => {
        const rect = element.getBoundingClientRect();
        return rect.width * rect.height;
      };
      return area(left) - area(right);
    });
  return candidates[0] || null;
}

export function bossConversationEvidenceText(pageText: string): string {
  const region = findBossConversationRegion();
  if (region)
    return normalizeBossText(region.innerText || region.textContent || "");
  const normalized = normalizeBossText(pageText);
  const marker = normalized.lastIndexOf("沟通职位");
  return marker >= 0 ? normalized.slice(marker) : "";
}

function actionSet(text: string): BossChatAction[] {
  const actions: BossChatAction[] = [];
  if (/简历请求已发送|简历已接收|已获取到简历/.test(text))
    actions.push("RESUME_REQUEST");
  if (/电话(?:交换)?请求已发送|微信(?:交换)?请求已发送/.test(text))
    actions.push("CONTACT_EXCHANGE");
  if (/面试邀请已发送|面试时间[：:]|面试已安排|已约面试/.test(text))
    actions.push("INTERVIEW_INVITE");
  // A rejection template is not evidence. Require the delivery marker or the
  // explicit BOSS state which is only rendered after the action succeeds.
  if (
    /已拒绝该候选人/.test(text) ||
    /不好意思[，, ]*不太合适哦.{0,30}送达|送达.{0,30}不好意思[，, ]*不太合适哦/.test(
      text,
    )
  )
    actions.push("REJECTION");
  if (/(?:已录用|录用成功|已入职)(?!人数)/.test(text)) actions.push("HIRED");
  return actions;
}

export function summarizeBossConversation(
  pageText: string,
  conversationUpdatedAt?: string,
): BossChatSummary {
  const evidenceText = bossConversationEvidenceText(pageText);
  if (!evidenceText)
    return {
      status: "NONE",
      evidenceText: "",
      hasRecruiterOutbound: false,
      actions: [],
    };
  const deliveryCount = [...evidenceText.matchAll(OUTBOUND_DELIVERY)].length;
  const systemCount = [...evidenceText.matchAll(SENT_SYSTEM_ACTION)].length;
  const recruiterMessageCount = deliveryCount + systemCount;
  const actions = actionSet(evidenceText);
  const attachmentEvidence = hasConversationResumeAttachment(evidenceText);
  const hasRecruiterOutbound = recruiterMessageCount > 0 || actions.includes("HIRED") || attachmentEvidence;
  if (attachmentEvidence && !actions.includes("RESUME_REQUEST")) actions.push("RESUME_REQUEST");
  return {
    status: hasRecruiterOutbound ? "CONFIRMED" : "NONE",
    evidenceText,
    hasRecruiterOutbound,
    ...(hasRecruiterOutbound
      ? {
          recruiterMessageCount: Math.max(1, recruiterMessageCount),
          lastRecruiterMessageAt: conversationUpdatedAt,
        }
      : {}),
    actions,
  };
}
