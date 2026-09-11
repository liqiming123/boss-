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
        !["GET_PAGE_ACCOUNT", "EXTENSION_LOGGED_OUT"].includes((message as { type: string }).type)
        )
        return false;
      if ((message as { type: string }).type === "EXTENSION_LOGGED_OUT") {
        generation++;
        stop();
        sendResponse({ ok: true });
        return false;
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
