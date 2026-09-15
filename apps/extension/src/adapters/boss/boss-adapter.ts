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
  classifyBossOutgoingMessage,
  classifyBossStatus,
  resolveBossStatusEvidence,
} from "./boss-status";
import { collectBossNativeCommunicationHistory } from "./boss-native-history";
import { isBossHostname } from "./boss-hosts";
import { bossOutgoingBubbleText, findBossConversationRegion, summarizeBossConversation } from "./boss-chat";
import { openBossCommunicationHistory } from "./boss-chat";
import { delay } from "../../shared/delay";
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
// The communication list is also rendered on BOSS's intention/interaction
// views. Those pages have no active candidate detail, but they are valid
// scan surfaces for the account-level background catch-up.
const chatShellPage = () =>
  /\/web\/chat\/(?:index|interaction|intention)(?:$|[?#])/.test(
    location.pathname + location.search,
  );
const candidateDetailPage = () => {
  const text = pageText();
  // Age is optional on some BOSS profiles and may arrive after the rest of
  // the profile card. Do not reject an otherwise valid detail page merely
  // because that one field is hidden or still loading.
  return chatShellPage() && /沟通(?:的)?职位/.test(text);
};
const lines = (text: string) =>
  text
    .replace(/\u00a0/g, " ")
    .split(/\n+/)
    .map((v) => normalizeBossText(v))
    .filter(Boolean);
// BOSS mixes list controls into the same text flow as the profile card. The
// virtualized conversation list ends with a load-more control, and BOSS can
// render that control immediately above the profile line, so a placeholder
// must never be accepted as a candidate identity. A row synced as
// "滚动加载更多" is not a person and permanently pollutes the Feishu table.
const BOSS_UI_TEXT =
  /^(?:滚动加载更多|点击加载更多|加载更多|查看更多|展开更多|收起|展开|没有更多了?|暂无更多|暂无数据|没有相关数据|暂无信息|加载中|正在加载|请稍候|未登录|请先登录|立即登录|刷新|换一换|下载APP|使用说明|意见反馈|帮助中心|回到顶部|全部职位|知道了|我知道了|确定|取消)$/;
const isBossUiText = (value: string) =>
  BOSS_UI_TEXT.test(value) || /加载更多|查看更多|暂无数据|没有更多|暂无更多/.test(value);
// The age-less profile fallback scans the lines immediately before experience
// or education. On the real chat page BOSS can interleave the latest message
// bubble with those profile fields, so a short all-Chinese sentence (for
// example “有剧本吗”) otherwise looks exactly like a Chinese name. Keep this
// list semantic and deliberately narrow: it blocks profile labels and obvious
// conversational clauses while still allowing uncommon short display names.
const BOSS_NON_NAME_TEXT =
  /(?:未填写|工作经历|求职意向|个人优势|测试时间|时间限制|联系方式|微信|手机号|简历|招聘|职位|沟通|好的.*感谢|谢谢|感谢|您好|你好|请问)|(?:吗|呢|呀|吧)[？?]?$/;
const isName = (value: string) =>
  /^[\u4e00-\u9fff·]{2,20}$/.test(value.replace(/[ \t]+(?=[\u4e00-\u9fff·])/g, "")) &&
  !isBossUiText(value.replace(/[ \t]+/g, "")) &&
  !BOSS_NON_NAME_TEXT.test(value.replace(/[ \t]+/g, "")) &&
  !/(本科|硕士|大专|活跃|昨天|今天|刚刚|全部|未读)/.test(value.replace(/[ \t]+/g, ""));
const canonicalName = (value: string) =>
  value.replace(/[ \t]+(?=[\u4e00-\u9fff·])/g, "");
// `26届` and `26年毕业` are graduation cohorts, not years of experience. The
// generic `N年` pattern used to read the graduation year as work experience,
// producing impossible profiles such as a 24-year-old with "26年" experience
// and a 20-year-old with "27年". Graduation phrases are removed before the
// experience pattern runs, and a graduation-only profile reports the cohort.
const graduationText = /(\d{2,4})\s*年\s*(?:毕业生|毕业时间|毕业|应届生|应届)/g;
const cohortText = /(\d{2,4})\s*届(?:毕业生|生)?/g;
const cohortLabel = (value: string) =>
  `${String(Number(value) % 100).padStart(2, "0")}届`;
function parseExperience(value: string): string | undefined {
  const graduation = value.match(/(\d{2,4})\s*年\s*(?:毕业生|毕业时间|毕业|应届生|应届)/);
  const cohort = value.match(/(\d{2,4})\s*届(?:毕业生|生)?/);
  const scrubbed = value.replace(graduationText, " ").replace(cohortText, " ");
  // A bare `N年` must not be a calendar year: a 4-digit year (`2026年`) or a
  // date fragment (`2026年09月`) is never years of experience.
  const explicit = scrubbed.match(
    /(?:工作经验\s*[:：]?\s*)?(\d+\s*[-至~]\s*\d+\s*年(?:以上)?|\d+\s*年以上|应届生|应届|在校生|无经验|经验不限|(?<!\d)\d{1,2}\s*年(?!\s*[\d.]*\s*[月日]))/,
  );
  if (explicit?.[1])
    return explicit[1].replace(/\s*[-至~]\s*/g, "-").replace(/\s+/g, "");
  const year = graduation?.[1] ?? cohort?.[1];
  return year ? cohortLabel(year) : undefined;
}
// BOSS sometimes renders the candidate's profile tail or the first chat line in
// the same text run as the job label (`财务主管 最近关注：无锡`,
// `直播助播 您好,我想和您沟通下…`). Neither belongs to the job name, so cut at
// the first conversational marker. NFKC has already turned full-width
// punctuation into ASCII by this point, hence both forms in the classes.
const JOB_TRAILING_LABEL =
  /\s*(?:最近关注|最近登录|最近沟通|求职意向|期望|薪资|工作地点|到岗时间)\s*[：:][\s\S]*$/;
const JOB_TRAILING_PLAIN = /\s*(?:最近关注|最近登录|已读|未读|送达)\s*[\s\S]*$/;
const JOB_CHAT_TAIL =
  /[，,。；;！!？?][\s\S]*$|\s+(?:您好|你好|请问|期待|方便|我们|目前|我是|有意向|在吗|看到)[\s\S]*$/;
const JOB_PROFILE_TAIL = /\s+[\u4e00-\u9fff·]{2,20}\s+\d{1,2}\s*岁[\s\S]*$/;
function cleanJobName(value: string) {
  return value
    .replace(JOB_TRAILING_LABEL, "")
    .replace(JOB_TRAILING_PLAIN, "")
    .replace(JOB_CHAT_TAIL, "")
    .replace(JOB_PROFILE_TAIL, "")
    .replace(/\s+/g, " ")
    .trim();
}
function candidateName(text: string) {
  const values = lines(text);
  const isUiLine = (value: string) => isBossUiText(value.replace(/[ \t]+/g, ""));
  for (let i = 0; i < values.length; i++) {
    if (!/^\d{1,2}\s*岁$/.test(values[i])) continue;
    const nearby = values.slice(i + 1, i + 5).join(" ");
    if (!/(本科|硕士|大专|博士)/.test(nearby)) continue;
    for (let j = i - 1; j >= Math.max(0, i - 4); j--) {
      // A control immediately above the profile line means this age belongs to
      // a list/section boundary, not to the candidate being opened. Stop
      // instead of walking further back into unrelated account text.
      if (isUiLine(values[j])) break;
      if (isName(values[j])) return canonicalName(values[j]);
    }
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
    : (() => {
        const values = lines(text);
        for (let i = 0; i < values.length; i++) {
          if (!/(\d{2}\s*届|应届生|无经验|经验不限|工作经验|\d+\s*年|中专|高中|中技|技校|大专|专科|本科|学士|硕士|MBA|博士|学历不限)/i.test(values[i])) continue;
          for (let j = i - 1; j >= Math.max(0, i - 4); j--) {
            if (isUiLine(values[j])) break;
            if (isName(values[j])) return canonicalName(values[j]);
          }
        }
        return "";
      })();
}
function jobName(text: string) {
  const normalized = normalizeBossText(text);
  const labeled = normalized.match(/沟通职位\s*[：:]\s*([^\n|]{1,120})/);
  if (labeled?.[1]) return cleanJobName(labeled[1]);
  const fallback = normalized.match(
    /沟通(?:的)?职位\s*[-—：:]\s*([^\n|]{1,100})/,
  );
  return fallback?.[1] ? cleanJobName(fallback[1]) : "";
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
  // The same name appears in the conversation list and message history.
  // Choose the occurrence with the strongest nearby profile evidence instead
  // of taking the last occurrence in the whole document.
  const occurrences = [...normalized.matchAll(new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"))];
  const windows = occurrences.map((match) => normalized.slice(Math.max(0, (match.index || 0) - 40), (match.index || 0) + 360));
  const nearby = windows
    .sort((a, b) => {
      const score = (value: string) =>
        (/(\d{1,3})\s*岁/.test(value) ? 4 : 0) +
        (parseExperience(value) !== undefined ? 3 : 0) +
        (/(中专|高中|中技|技校|大专|专科|本科|学士|硕士|MBA|博士)/i.test(value) ? 3 : 0);
      return score(b) - score(a);
    })[0] || normalized;
  const age = nearby.match(/(\d{1,3})\s*岁/);
  const education = nearby.match(
    /(中专|高中|中技|技校|大专|专科|本科|学士|硕士|MBA|博士|学历不限)/i,
  );
  return {
    age: age ? Number(age[1]) : undefined,
    experience: parseExperience(nearby),
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
    return candidateDetailPage();
  }
  async extractAccount(): Promise<Extraction<AccountData>> {
    if (!chatShellPage()) return unavailable("BOSS_UNSUPPORTED_PAGE");
    const value = accountName(pageText());
    return value
      ? { status: "OK", value: { displayName: value } }
      : unavailable();
  }
  async extractCandidate(): Promise<Extraction<CandidateData>> {
    if (!candidateDetailPage()) return unavailable("BOSS_UNSUPPORTED_PAGE");
    let text = pageText();
    // The BOSS communication drawer is collapsed by default on several
    // account/page variants. Open it automatically before extracting history;
    // recruiters should never have to click the native icon for deduplication.
    if (!/(?:我的沟通|同事沟通)/.test(text) && /沟通记录/.test(text)) {
      if (openBossCommunicationHistory()) {
        await delay(500);
        text = pageText();
      }
    }
    // Profile fields are loaded independently from the chat shell. Retry a
    // bounded number of times so slow/virtualized layouts get a stable card,
    // without keeping the page observer alive indefinitely.
    let value = candidateName(text);
    for (let attempt = 0; attempt < 8 && !value; attempt++) {
      await delay(250);
      text = pageText();
      value = candidateName(text);
    }
    if (!value) return unavailable();
    // Fail closed when the parser only sees a generic shell/profile fragment.
    // A candidate must have a bounded, human-readable name and a nearby
    // profile signal; otherwise a recruiter/account name can be persisted as
    // a candidate during asynchronous BOSS re-renders.
    if (
      value.length > 20 ||
      isBossUiText(value) ||
      /职位|沟通|简历|招聘|账号/.test(value)
    ) {
      return unavailable("BOSS_CANDIDATE_IDENTITY_UNCERTAIN");
    }
    const details = profile(text, value);
    const times = parseBossConversationTimes(text, value);
    const chat = summarizeBossConversation(text, times.conversationUpdatedAt);
    const outgoingText = bossOutgoingBubbleText();
    const nativeCommunications = collectBossNativeCommunicationHistory();
    const currentAccount = accountName(text);
    const nativeRecruiterOutbound = nativeCommunications.some(
      (item) => normalizeBossText(item.recruiterName) === normalizeBossText(currentAccount),
    );
    const passiveStatus = classifyBossStatus(
      chat.evidenceText,
      new Date().toISOString(),
      false,
    );
    const outgoingStatus = outgoingText
      ? classifyBossOutgoingMessage(outgoingText)
      : null;
    // Rejection no longer wins unconditionally: the last signal in the whole
    // conversation decides, so a newer invitation supersedes an older
    // rejection instead of being locked out by it. See
    // resolveBossStatusEvidence for the full rule.
    const statusEvidence = resolveBossStatusEvidence(
      passiveStatus,
      outgoingStatus,
    );
    return {
      status: "OK",
      value: {
        displayName: value,
        ...details,
        ...times,
        hasRecruiterOutbound: chat.hasRecruiterOutbound || nativeRecruiterOutbound,
        // Candidate profile/resume text may contain interview wishes. Those
        // are not a recruiter-side status transition.
        statusEvidence,
        historicalJobs: bossHistoricalJobs(text),
        nativeCommunications,
        ...resumeData(text),
      },
    };
  }
  async extractJob(): Promise<Extraction<JobData>> {
    if (!candidateDetailPage()) return unavailable("BOSS_UNSUPPORTED_PAGE");
    const value = jobName(pageText());
    // A BOSS job name is short. Anything longer is a polluted capture from a
    // layout we have not seen yet, so fail closed instead of storing it.
    if (!value || value.length > 60 || /^(沟通职位|期望|薪资)$/.test(value)) {
      return unavailable("BOSS_JOB_IDENTITY_UNCERTAIN");
    }
    return { status: "OK", value: { displayName: value } };
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
