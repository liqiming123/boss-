import { MockRecruitmentAdapter } from "../adapters/mock/mock-adapter";
import { BossAdapter } from "../adapters/boss/boss-adapter";
import { PageController } from "./page-controller";
import { runCompanyDailyPage } from "./company-daily-entry";
import { isReportUrl } from "../adapters/boss/company-daily";
if (isReportUrl(location.href)) void runCompanyDailyPage();
// One sanitized report per page load when the header account name cannot be
// parsed while a chat-shell URL is open. This is the only server-visible trace
// of the "popup says 请刷新沟通页 although the page is fine" state; without it
// the failure is indistinguishable from a stale tab that has no script at all.
let accountReadReported = false;
async function reportAccountReadIssue(adapter: typeof BossAdapter.prototype | typeof MockRecruitmentAdapter.prototype) {
  if (accountReadReported) return;
  accountReadReported = true;
  try {
    const diagnostic = await adapter.getDiagnostics();
    await chrome.runtime.sendMessage({
      type: "SEND_DIAGNOSTIC",
      payload: {
        platform: diagnostic.platform,
        adapter_version: diagnostic.adapterVersion,
        page_type: diagnostic.pageType,
        account_status: diagnostic.accountStatus,
        candidate_status: diagnostic.candidateStatus,
        job_status: diagnostic.jobStatus,
        platform_id_status: diagnostic.platformIdStatus,
        error_codes: [...new Set([...diagnostic.errorCodes, "BOSS_ACCOUNT_NAME_UNREADABLE"])],
        sanitized_context: { ...diagnostic.sanitizedContext, accountRead: true },
      },
    });
  } catch {
    // Diagnostics are best-effort and must never surface to the recruiter.
  }
}
const adapter = [new MockRecruitmentAdapter(), new BossAdapter()].find((item) =>
  item.canHandle(location.href),
);
/**
 * Upload the shape-only diagnostic when this page cannot read its account name.
 *
 * A page that never answers the popup leaves no trace anywhere: the popup says
 * "read the account name", the server sees nothing, and the tab is
 * indistinguishable from one where the extension was never loaded. Retry while
 * the shell paints, then report once, so the next occurrence is diagnosable
 * without asking the recruiter to open the popup or a console.
 */
async function reportUnreadableAccountAtStartup() {
  if (!adapter) return;
  for (let attempt = 0; attempt < 10; attempt++) {
    // The header renders in stages; an early read is not a failure.
    await new Promise((resolve) => window.setTimeout(resolve, 3000));
    const result = await adapter.extractAccount();
    if (result.status === "OK") return;
  }
  await reportAccountReadIssue(adapter);
}
if (adapter) {
  let controller: PageController | undefined;
  let dispose: (() => void) | undefined;
  let generation = 0;
  function stop() {
    controller?.stopCatchup();
    dispose?.();
    controller = undefined;
    dispose = undefined;
  }
  async function reconcileLogin() {
    const revision = ++generation;
    const auth = await chrome.storage.local.get(["accessToken"]);
    if (revision !== generation) return;
    if (!auth.accessToken) { stop(); return; }
    if (!controller && !isReportUrl(location.href)) {
      controller = new PageController(adapter!);
      dispose = controller.start();
    }
  }
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && "accessToken" in changes) void reconcileLogin();
  });
  void reconcileLogin();
  void reportUnreadableAccountAtStartup();
  chrome.runtime.onMessage.addListener(
    (message: unknown, _sender, sendResponse) => {
      if (
        !message ||
        typeof message !== "object" ||
        !("type" in message) ||
        !["GET_PAGE_ACCOUNT", "RUN_CATCHUP", "LIVE_DUPLICATE_ALERT", "EXTENSION_LOGGED_OUT"].includes((message as { type: string }).type)
        )
        return false;
      if ((message as { type: string }).type === "EXTENSION_LOGGED_OUT") {
        generation++;
        stop();
        sendResponse({ ok: true });
        return false;
      }
      if ((message as { type: string }).type === "LIVE_DUPLICATE_ALERT") {
        // A pushed alert is rendered only when this tab is actually showing the
        // named candidate; the controller re-reads the page identity to check.
        const payload = (message as { payload?: Record<string, unknown> }).payload ?? {};
        if (!controller) {
          sendResponse({ ok: true, data: { shown: false } });
          return false;
        }
        void controller
          .handleLiveAlert(payload)
          .then((shown) => sendResponse({ ok: true, data: { shown } }))
          .catch(() => sendResponse({ ok: true, data: { shown: false } }));
        return true;
      }
      if ((message as { type: string }).type === "RUN_CATCHUP") {
        // The popup can ask for an immediate pass; polling is anchor-free, so
        // this is just "scan the 沟通中 list now" rather than a watermark reset.
        if (!controller) {
          sendResponse({ ok: false, error: "请先打开 BOSS 沟通页" });
          return false;
        }
        void controller
          .runCatchupNow()
          .then(() => sendResponse({ ok: true }))
          .catch(() =>
            sendResponse({ ok: false, error: "补扫启动失败" }),
          );
        return true;
      }
      void adapter
        .extractAccount()
        .then((result) => {
          if (result.status !== "OK") void reportAccountReadIssue(adapter);
          sendResponse(
            result.status === "OK"
              ? { ok: true, displayName: result.value.displayName }
              : { ok: false, error: result.errorCode },
          );
        });
      return true;
    },
  );
}
