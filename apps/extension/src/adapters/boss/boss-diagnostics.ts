import { bossSelectors } from "./boss-selectors";
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
    },
  };
}
