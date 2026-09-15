import { MockRecruitmentAdapter } from "../adapters/mock/mock-adapter";
import { BossAdapter } from "../adapters/boss/boss-adapter";
import { PageController } from "./page-controller";
import { runCompanyDailyPage } from "./company-daily-entry";
import { isReportUrl } from "../adapters/boss/company-daily";
if (isReportUrl(location.href)) void runCompanyDailyPage();
const adapter = [new MockRecruitmentAdapter(), new BossAdapter()].find((item) =>
  item.canHandle(location.href),
);
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
        .then((result) =>
          sendResponse(
            result.status === "OK"
              ? { ok: true, displayName: result.value.displayName }
              : { ok: false, error: result.errorCode },
          ),
        );
      return true;
    },
  );
}
