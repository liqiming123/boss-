import { afterEach, expect, it } from "vitest";
import { PanelController } from "../src/content/panel-controller";

afterEach(() => document.querySelector("#recruitment-collab-host")?.remove());

it("keeps scan status through ordinary hide and status renders until completion", () => {
  const panel = new PanelController();
  const host = document.querySelector<HTMLElement>("#recruitment-collab-host")!;
  panel.showCatchupStatus();
  panel.hide();
  expect(host.style.display).toBe("block");
  expect(host.shadowRoot!.textContent).toContain("后台补扫进行中");
  panel.showLookupUnavailable();
  expect(host.shadowRoot!.textContent).toContain("后台补扫进行中");
  panel.hideCatchupStatus();
  expect(host.style.display).toBe("none");
});

it("does not reopen after logout even when old callbacks render late", () => {
  const panel = new PanelController();
  const host = document.querySelector<HTMLElement>("#recruitment-collab-host")!;
  panel.showCatchupStatus();
  panel.dispose();
  panel.showLookupUnavailable();
  panel.showCatchupStatus();
  expect(host.style.display).toBe("none");
  expect(host.shadowRoot!.innerHTML).toBe("");
});
