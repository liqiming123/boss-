import type {
  AccountData,
  CandidateData,
  Extraction,
  JobData,
  RecruitmentSiteAdapter,
  ResumePreviewOpened,
} from "../types";
import { observeBossPage } from "./boss-observer";
import {
  observeBossRecruiterMessageSent,
  observeBossResumePreviewOpened,
} from "./boss-message-observer";
import { bossDiagnostic } from "./boss-diagnostics";
import { normalizeBossText } from "./boss-normalizers";
import { parseBossConversationTimes } from "./boss-times";
import {
  bossHistoricalJobs,
  classifyBossStatus,
} from "./boss-status";
import { collectBossNativeCommunicationHistory } from "./boss-native-history";
import { isBossHostname } from "./boss-hosts";
import { findBossConversationRegion, summarizeBossConversation } from "./boss-chat";
const unavailable = <T>(code = "BOSS_FIELDS_NOT_FOUND"): Extraction<T> => ({
  status: "ERROR",
  errorCode: code,
});
const pageText = () =>
  typeof document === "undefined"
    ? ""
    : (document.body?.innerText || document.body?.textContent || "").replace(
        /\u00a0/g,
        " ",
      );
const chatPage = () =>
  /\/web\/chat\/index(?:$|[?#])/.test(location.pathname + location.search);
const lines = (text: string) =>
  text
    .replace(/\u00a0/g, " ")
    .split(/\n+/)
    .map((v) => normalizeBossText(v))
    .filter(Boolean);
const isName = (value: string) =>
  /^[\u4e00-\u9fff·]{2,20}$/.test(value.replace(/[ \t]+(?=[\u4e00-\u9fff·])/g, "")) &&
  !/(职位|沟通|简历|本科|硕士|大专|活跃|昨天|今天|刚刚|全部|未读)/.test(value.replace(/[ \t]+/g, ""));
const canonicalName = (value: string) =>
  value.replace(/[ \t]+(?=[\u4e00-\u9fff·])/g, "");
function candidateName(text: string) {
  const values = lines(text);
  for (let i = 0; i < values.length; i++) {
    if (!/^\d{1,2}\s*岁$/.test(values[i])) continue;
    const nearby = values.slice(i + 1, i + 5).join(" ");
    if (!/(本科|硕士|大专|博士)/.test(nearby)) continue;
    for (let j = i - 1; j >= Math.max(0, i - 4); j--)
      if (isName(values[j])) return canonicalName(values[j]);
  }
  const compact = normalizeBossText(text).match(
    /([\u4e00-\u9fff·]{2,20})\s+(?:刚刚活跃|在线|昨天|今天)?\s*\d{1,2}\s*岁[\s\S]{0,30}(?:本科|硕士|大专|博士)/,
  );
  if (compact && isName(compact[1])) return canonicalName(compact[1]);
  const profileMatches = [
    ...normalizeBossText(text).matchAll(
      /([\u4e00-\u9fff·]{2,20})\s+(?:刚刚活跃|在线|昨天|今天)?\s*\d{1,2}\s*岁/g,
    ),
  ].filter((match) => isName(match[1]));
  return profileMatches.at(-1)?.[1]
    ? canonicalName(profileMatches.at(-1)![1])
    : "";
}
function jobName(text: string) {
  const normalized = normalizeBossText(text);
  const labeled = normalized.match(/沟通职位\s*[：:]\s*([^\n|]{1,120})/);
  if (labeled?.[1])
    return labeled[1]
      .replace(/\s*(?:期望|薪资)[:：].*$/, "")
      .replace(/\s+[\u4e00-\u9fff·]{2,20}\s+\d{1,2}\s*岁[\s\S]*$/, "")
      .trim();
  const fallback = normalized.match(
    /沟通(?:的)?职位\s*[-—：:]\s*([^\n|]{1,100})/,
  );
  return (
    fallback?.[1]
      ?.replace(/\s*(?:期望|薪资)[:：].*$/, "")
      .replace(/\s+[\u4e00-\u9fff·]{2,20}\s+\d{1,2}\s*岁[\s\S]*$/, "")
      .trim() || ""
  );
}
function accountName(text: string) {
  const values = lines(text);
  const marker = values.findIndex(
    (value) => value === "升级VIP" || value === "账号权益",
  );
  if (marker >= 0)
    for (const value of values.slice(marker + 1, marker + 4))
      if (isName(value)) return value;
  const normalized = normalizeBossText(text);
  const labeled = normalized.match(
    /(?:账号权益|升级VIP)\s*[|｜ ]+([\u4e00-\u9fff·]{1,20}(?:先生|女士))/,
  );
  if (labeled?.[1]) return labeled[1].trim();
  const bare = normalized.match(
    /(?:账号权益|升级VIP)\s+([\u4e00-\u9fff·]{2,20})(?:\s|$)/,
  )?.[1];
  if (bare && isName(bare)) return bare;
  const top = values.slice(0, 240).find((value) =>
    /^[\u4e00-\u9fff·]{1,20}\s*(?:先生|女士)$/.test(value),
  );
  if (top) return top.replace(/\s+(?=先生|女士)/, "");
  if (typeof document !== "undefined") {
    const accountNode = [...document.querySelectorAll<HTMLElement>(
      '[class*="account" i], [class*="user" i], [class*="recruit" i], [class*="boss" i]',
    )].find((node) => {
      const text = normalizeBossText(node.innerText || node.textContent || "");
      return /^[\u4e00-\u9fff·]{1,20}\s*(?:先生|女士)$/.test(text);
    });
    if (accountNode)
      return normalizeBossText(accountNode.innerText || accountNode.textContent || "").replace(
        /\s+(?=先生|女士)/,
        "",
      );
    // BOSS occasionally renders the top-right account as a plain text node
    // with no stable class name. Limit the fallback to the top-right quadrant
    // so candidate names in the conversation/list are not selected.
    const topRight = [...document.querySelectorAll<HTMLElement>("body *")]
      .map((node) => ({ node, text: normalizeBossText(node.textContent || "") }))
      .filter(({ node, text }) => {
        const rect = node.getBoundingClientRect();
        const compact = text.replace(/[\s\u200b\ufeff]+/g, "");
        return (
          node.children.length <= 2 &&
          /^[\u4e00-\u9fff·]{1,20}(?:先生|女士)?$/.test(compact) &&
          rect.top >= 0 &&
          rect.top < 260 &&
          rect.left > window.innerWidth * 0.55
        );
      })
      .sort((a, b) => a.node.getBoundingClientRect().top - b.node.getBoundingClientRect().top)[0];
    if (topRight) return topRight.text.replace(/[\s\u200b\ufeff]+/g, "");
    // Some BOSS builds split the account name across sibling spans and put
    // badges/icons inside the same header. Inspect compact, visible
    // right-hand header containers as a final fallback.
    const headerName = [...document.querySelectorAll<HTMLElement>("body *")]
      .map((node) => ({ node, text: normalizeBossText(node.innerText || node.textContent || "") }))
      .filter(({ node, text }) => {
        const rect = node.getBoundingClientRect();
        const compact = text.replace(/[\s\u200b\ufeff|｜·•]+/g, "");
        return (
          node.children.length >= 2 &&
          compact.length <= 24 &&
          /^[\u4e00-\u9fff]{1,20}(?:先生|女士)$/.test(compact) &&
          rect.top >= 0 && rect.top < 260 && rect.left > window.innerWidth * 0.55
        );
      })
      .sort((a, b) => a.node.getBoundingClientRect().top - b.node.getBoundingClientRect().top)[0];
    if (headerName) return headerName.text.replace(/[\s\u200b\ufeff|｜·•]+/g, "");
  }
  return "";
}
function profile(text: string, name: string) {
  const normalized = normalizeBossText(text);
  const index = normalized.lastIndexOf(name);
  const nearby = index >= 0 ? normalized.slice(index, index + 240) : normalized;
  const age = nearby.match(/(\d{1,2})\s*岁/);
  const experience = nearby.match(
    /(?:^|\s)(应届生|无经验|经验不限|\d{1,2}\s*年)(?:\s|$)/,
  );
  const education = nearby.match(
    /(?:中专|高中|大专|专科|本科|学士|硕士|MBA|博士)/i,
  );
  return {
    age: age ? Number(age[1]) : undefined,
    experience: experience?.[1]?.replace(/\s/g, ""),
    education: education?.[0],
  };
}
function resumeData(text: string) {
  const hasAttachment = /附件简历|在线简历/.test(text);
  const root = findBossConversationRegion() || document;
  const anchor = [
    ...root.querySelectorAll<HTMLAnchorElement>("a[href]"),
  ].find((item) =>
    /(?:在线|预览|查看附件|附件)简历/.test(
      normalizeBossText(
        item.innerText || item.getAttribute("aria-label") || "",
      ),
    ),
  );
  if (!anchor) return { resumeStatus: hasAttachment ? "附件简历可用" : "NONE" };
  try {
    const url = new URL(anchor.href, location.href);
    if (url.protocol !== "https:" || !isBossHostname(url.hostname))
      return { resumeStatus: "附件简历可用" };
    return {
      resumeStatus: "附件简历可用",
      resumeDownload: {
        url: url.href,
        fileName: decodeURIComponent(
          url.pathname.split("/").pop() || "candidate-resume.pdf",
        ).slice(0, 180),
      },
    };
  } catch {
    return { resumeStatus: "附件简历可用" };
  }
}
export class BossAdapter implements RecruitmentSiteAdapter {
  readonly platform = "boss";
  canHandle(url: string) {
    try {
      return isBossHostname(new URL(url).hostname);
    } catch {
      return false;
    }
  }
  isCandidateConversationPage() {
    return chatPage();
  }
  async extractAccount(): Promise<Extraction<AccountData>> {
    if (!chatPage()) return unavailable("BOSS_UNSUPPORTED_PAGE");
    const value = accountName(pageText());
    return value
      ? { status: "OK", value: { displayName: value } }
      : unavailable();
  }
  async extractCandidate(): Promise<Extraction<CandidateData>> {
    if (!chatPage()) return unavailable("BOSS_UNSUPPORTED_PAGE");
    const text = pageText(),
      value = candidateName(text);
    if (!value) return unavailable();
    const details = profile(text, value);
    const times = parseBossConversationTimes(text, value);
    const chat = summarizeBossConversation(text, times.conversationUpdatedAt);
    return {
      status: "OK",
      value: {
        displayName: value,
        ...details,
        ...times,
        hasRecruiterOutbound: chat.hasRecruiterOutbound,
        // Candidate profile/resume text may contain interview wishes. Those
        // are not a recruiter-side status transition.
        statusEvidence: classifyBossStatus(
          chat.evidenceText,
          new Date().toISOString(),
          false,
        ),
        historicalJobs: bossHistoricalJobs(text),
        nativeCommunications: collectBossNativeCommunicationHistory(),
        ...resumeData(text),
      },
    };
  }
  async extractJob(): Promise<Extraction<JobData>> {
    if (!chatPage()) return unavailable("BOSS_UNSUPPORTED_PAGE");
    const value = jobName(pageText());
    return value
      ? { status: "OK", value: { displayName: value } }
      : unavailable();
  }
  observePageChange(callback: () => void) {
    return observeBossPage(callback);
  }
  observeRecruiterMessageSent(
    callback: Parameters<
      RecruitmentSiteAdapter["observeRecruiterMessageSent"]
    >[0],
  ) {
    return observeBossRecruiterMessageSent(callback);
  }
  observeResumePreviewOpened(callback: (event: ResumePreviewOpened) => void) {
    return observeBossResumePreviewOpened(callback);
  }
  async getDiagnostics() {
    return bossDiagnostic();
  }
}
