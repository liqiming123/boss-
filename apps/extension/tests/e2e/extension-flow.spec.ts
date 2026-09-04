import { chromium, expect, test } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

test("mock site stays silent until the selected candidate changes", async () => {
  const profile = await mkdtemp(join(tmpdir(), "recruitment-extension-"));
  const extensionPath = resolve("dist");
  const context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    args: [
      `--disable-extensions-except=${extensionPath}`,
      `--load-extension=${extensionPath}`,
    ],
  });
  try {
    let worker = context.serviceWorkers()[0];
    if (!worker) worker = await context.waitForEvent("serviceworker");
    await worker.evaluate(async () => {
      await chrome.storage.local.set({
        apiBaseUrl: "http://localhost:8000/api/v1",
      });
    });

    const page = await context.newPage();
    await page.goto("http://localhost:5174/candidate/mock-xm-a");
    const panel = page.locator("#recruitment-collab-host");
    await expect(panel).toBeHidden();

    await page.evaluate(() => {
      const name = document.querySelector<HTMLElement>("[data-candidate-name]");
      if (name) name.textContent = "小明2";
    });
    // A candidate with no duplicate evidence remains silent after syncing.
    await page.waitForTimeout(1_200);
    await expect(panel).toBeHidden();
  } finally {
    await context.close();
    await rm(profile, { recursive: true, force: true });
  }
});
