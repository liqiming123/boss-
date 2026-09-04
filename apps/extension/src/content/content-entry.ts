import { MockRecruitmentAdapter } from "../adapters/mock/mock-adapter";
import { BossAdapter } from "../adapters/boss/boss-adapter";
import { PageController } from "./page-controller";
const adapter = [new MockRecruitmentAdapter(), new BossAdapter()].find((item) =>
  item.canHandle(location.href),
);
if (adapter) {
  new PageController(adapter).start();
  chrome.runtime.onMessage.addListener(
    (message: unknown, _sender, sendResponse) => {
      if (
        !message ||
        typeof message !== "object" ||
        !("type" in message) ||
        (message as { type: string }).type !== "GET_PAGE_ACCOUNT"
      )
        return false;
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
