import { normalizeBossText } from "./boss-normalizers";
import { BOSS_INVITE_SENT_MARKER } from "./boss-status";

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

const SENT_SYSTEM_ACTION = new RegExp(
  `(?:简历请求已发送|电话(?:交换)?请求已发送|微信(?:交换)?请求已发送|${BOSS_INVITE_SENT_MARKER.source})`,
  "g",
);
const CHAT_REGION_EVIDENCE = new RegExp(
  `送达|请求已发送|点击预览附件简历|${BOSS_INVITE_SENT_MARKER.source}`,
);
const INVITE_ACTION = new RegExp(
  `(?:${BOSS_INVITE_SENT_MARKER.source}|面试时间[：:]|面试已安排|已约面试)`,
);
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
      // The candidate profile card also contains “沟通职位”, but it is not
      // the conversation. A real conversation region must expose the
      // composer or a concrete chat delivery/attachment marker; otherwise
      // profile/experience cards are rejected before snapshot selection.
      const hasComposer = !!element.querySelector(
        'textarea,input,[contenteditable="true"]',
      );
      const hasChatEvidence =
        CHAT_REGION_EVIDENCE.test(text) ||
        !!element.querySelector(
          '[aria-label*="附件简历"],[title*="附件简历"],[aria-label*="在线简历"]',
        );
      return (
        visibleRect(element) &&
        rect.left > viewportWidth * 0.28 &&
        rect.width > 360 &&
        rect.height > 150 &&
        /沟通(?:的)?职位|送达|请求已发送|发送了面试邀请|面试邀请已发送|邀请已发送/.test(text) &&
        (hasComposer || hasChatEvidence) &&
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

/**
 * Some BOSS variants omit all delivery/system labels. In those variants the
 * only stable signal is the chat composer plus right-aligned recruiter
 * bubbles. Keep this as a geometry/evidence fallback rather than depending on
 * account-specific class names.
 */
function fallbackConversationRegion(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  const width = document.documentElement.clientWidth || window.innerWidth;
  const composer = [...document.querySelectorAll<HTMLElement>(
    'textarea,input,[contenteditable="true"]',
  )].find((node) => {
    const rect = node.getBoundingClientRect();
    return rect.width > 180 && rect.left > width * 0.28 && rect.bottom > 0;
  });
  if (!composer) return null;
  let node: HTMLElement | null = composer.parentElement;
  while (node && node !== document.body) {
    const rect = node.getBoundingClientRect();
    const text = normalizeBossText(node.innerText || node.textContent || "");
    if (rect.left > width * 0.25 && rect.width > 360 && rect.height > 180 &&
        /(?:发送|沟通|候选人|职位)/.test(text)) return node;
    node = node.parentElement;
  }
  return null;
}

export function bossConversationEvidenceText(pageText: string): string {
  const region = findBossConversationRegion() || fallbackConversationRegion();
  if (region)
    return normalizeBossText(region.innerText || region.textContent || "");
  const normalized = normalizeBossText(pageText);
  const marker = normalized.lastIndexOf("沟通职位");
  return marker >= 0 ? normalized.slice(marker) : "";
}

/** Conversation-region chrome: quick actions, system labels and controls that
 * are rendered right-aligned like a recruiter bubble but are not a message.
 * Classifying them as text is what turned the “不合适” button into a rejection
 * that then locked a candidate out of 已约面. */
const CHAT_UI_TEXT =
  /^(?:不合适|求简历|换电话|换微信|查看面试|发送|在线简历|附件简历|举报|屏蔽|发送了面试邀请|你撤回了一条消息|以上是打招呼的内容|沟通职位|我的沟通|同事沟通|面试|电话|微信|简历)$/;

/** Text that cannot be a message body: chrome, controls or a status label. */
export function isBossChatUiText(value: string): boolean {
  const text = normalizeBossText(value).replace(/[：:]\s*$/, "").trim();
  if (!text) return true;
  if (CHAT_UI_TEXT.test(text)) return true;
  if (text.length <= 8 && /^(?:不合适|求简历|换电话|换微信|查看面试|在线简历|附件简历|发送|沟通职位)$/.test(text)) {
    return true;
  }
  return /点击(?:预览|查看)|请输入|请选择|暂无更多|加载中|没有更多/.test(text);
}

/** Detect recruiter-side bubbles when BOSS does not render “送达”. */
export function bossOutgoingBubbleText(): string {
  const region = findBossConversationRegion() || fallbackConversationRegion();
  if (!region) return "";
  const root = region.getBoundingClientRect();
  const bubbles = [...region.querySelectorAll<HTMLElement>("div,li,p")].filter((node) => {
    // A message bubble is never an interactive control.
    if (interactive(node)) return false;
    const text = normalizeBossText(node.innerText || node.textContent || "");
    if (text.length < 1 || text.length > 500) return false;
    if (isBossChatUiText(text)) return false;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.width > root.width * 0.9) return false;
    const style = getComputedStyle(node);
    const rightAligned = rect.left + rect.width / 2 > root.left + root.width * 0.58;
    const textAligned = style.textAlign === "right";
    const colors = `${style.backgroundColor} ${style.borderColor}`;
    // BOSS recruiter bubbles are rendered with a saturated cyan/teal fill in
    // the current desktop and Windows variants, while candidate bubbles are
    // neutral gray. Use color only as supporting evidence with right-side
    // geometry; never treat color alone as a message sender signal.
    const recruiterTint = /rgb\(\s*(?:[0-9]{1,2}|1[0-9]{2}|2[0-9]{2})\s*,\s*(?:1[4-9][0-9]|2[0-5][0-9])\s*,\s*(?:1[4-9][0-9]|2[0-5][0-9])\s*\)/.test(colors);
    const semantic = Object.entries(node.dataset).some(([key, value]) =>
      /sender|direction|owner|self|mine|from/i.test(key) && /self|mine|recruit|right|outgoing|send/i.test(value || ""),
    );
    return (rightAligned || textAligned) && (recruiterTint || semantic);
  });
  return [...new Set(bubbles.map((node) => normalizeBossText(node.innerText || node.textContent || "").trim()))].join("\n");
}

export function hasBossOutgoingBubbleEvidence(): boolean {
  return !!bossOutgoingBubbleText();
}

/** Open BOSS's collapsed communication drawer once so history is available
 * to the normal extractor. The lookup is text/geometry based and does not
 * depend on a volatile account-specific selector. */
export function openBossCommunicationHistory(): boolean {
  if (typeof document === "undefined") return false;
  const node = [...document.querySelectorAll<HTMLElement>(
    'button,[role="button"],a,span,div',
  )].find((item) => {
    const text = normalizeBossText(
      item.innerText || item.textContent || item.getAttribute("aria-label") || item.getAttribute("title") || "",
    ).trim();
    const rect = item.getBoundingClientRect();
    return /沟通记录/.test(text) && text.length <= 24 && rect.width > 0 && rect.height > 0 && rect.left > window.innerWidth * 0.25;
  });
  if (!node) return false;
  node.click();
  return true;
}

function actionSet(text: string): BossChatAction[] {
  const actions: BossChatAction[] = [];
  if (/简历请求已发送|简历已接收|已获取到简历/.test(text))
    actions.push("RESUME_REQUEST");
  if (/电话(?:交换)?请求已发送|微信(?:交换)?请求已发送/.test(text))
    actions.push("CONTACT_EXCHANGE");
  if (INVITE_ACTION.test(text))
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
  // A right-side bubble with the recruiter tint is a safe cross-layout
  // fallback when delivery text is virtualized away. Geometry alone is not
  // sufficient, so neutral candidate bubbles cannot trigger synchronization.
  const hasRecruiterOutbound = recruiterMessageCount > 0 || actions.includes("HIRED") || attachmentEvidence || hasBossOutgoingBubbleEvidence();
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
