import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  workers: 1,
  fullyParallel: false,
  globalTeardown: "./tests/e2e/cleanup.ts",
  webServer: [
    {
      command: "../../.venv/bin/python tests/e2e/start_api.py",
      url: "http://localhost:8000/api/v1/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "pnpm --filter @recruitment/mock-site dev",
      url: "http://localhost:5174",
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
  reporter: "list",
});
