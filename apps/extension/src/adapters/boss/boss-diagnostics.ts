import { bossSelectors } from "./boss-selectors";

/**
 * Shape-only description of the header lines the account parser inspects.
 *
 * A negative result cannot be debugged from a boolean: "the name is Latin",
 * "the name carries parentheses" and "the header never rendered" all look the
 * same. These facts separate them without shipping any page text: each line is
 * reported as its length plus the character classes it contains.
 */
function headerShape() {
  const values = (document.body?.innerText || "")
    .replace(/ /g, " ")
    .split(/\n+/)
    .map((value) => value.trim())
    .filter(Boolean);
  const marker = values.findIndex(
    (value) => value === "升级VIP" || value === "账号权益",
  );
  const nearby = marker >= 0 ? values.slice(marker + 1, marker + 4) : values.slice(0, 3);
  return {
    accountChipFound: marker >= 0,
    headerShapes: nearby.map((value) => `${value.length}:${charClasses(value)}`).join(" | "),
  };
}

/** The character classes present in a value, never the value itself. */
function charClasses(value: string) {
  const tags: string[] = [];
  if (/[一-鿿]/.test(value)) tags.push("CJK");
  if (/[A-Za-z]/.test(value)) tags.push("LATIN");
  if (/\d/.test(value)) tags.push("DIGIT");
  if (/[·•]/.test(value)) tags.push("DOT");
  if (/[（）()]/.test(value)) tags.push("PAREN");
  if (/[，,。.;；!！?？]/.test(value)) tags.push("PUNCT");
  if (/\s/.test(value)) tags.push("SPACE");
  if (/[^一-鿿A-Za-z0-9_\-·•（）()\s]/.test(value)) tags.push("OTHER");
  return tags.join("+") || "EMPTY";
}

export function bossDiagnostic() {
  const text = (document.body?.innerText || "").replace(/\s+/g, " ");
  const hasRecruiterShell = /职位管理|推荐牛人|牛人管理|招聘数据/.test(text);
  const hasChatLayout = /沟通|在线简历|沟通职位|期望：/.test(text);
  const hasCandidateSignals =
    /\d{2}岁/.test(text) && /本科|硕士|大专/.test(text);
  const hasJobSignals =
    /沟通职位\s*[：:]|沟通的?职位\s*[-—：:]|期望\s*[：:]/.test(text);
  const errors: string[] = [];
  if (!hasRecruiterShell) errors.push("BOSS_ACCOUNT_SHELL_NOT_FOUND");
  if (!hasCandidateSignals) errors.push("BOSS_CANDIDATE_SIGNALS_NOT_FOUND");
  if (!hasJobSignals) errors.push("BOSS_JOB_SIGNALS_NOT_FOUND");
  return {
    platform: "boss",
    adapterVersion: "boss-adapter-resilient-2",
    pageType: location.pathname,
    accountStatus: hasRecruiterShell ? "DETECTED" : "MISSING",
    candidateStatus: hasCandidateSignals ? "SIGNALS_PRESENT" : "MISSING",
    jobStatus: hasJobSignals ? "SIGNALS_PRESENT" : "MISSING",
    platformIdStatus: "UNCONFIGURED",
    errorCodes: errors,
    sanitizedContext: {
      configuredSelectorGroups: Object.values(bossSelectors).filter(
        (v) => v.length,
      ).length,
      pathHash: "not-collected",
      hasRecruiterShell,
      hasChatLayout,
      hasCandidateSignals,
      hasJobSignals,
      iframeCount: document.querySelectorAll("iframe").length,
      ...headerShape(),
    },
  };
}
