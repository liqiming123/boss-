import { afterEach, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ start: vi.fn(), stop: vi.fn(), dispose: vi.fn() }));
vi.mock("../src/adapters/mock/mock-adapter", () => ({ MockRecruitmentAdapter: class { canHandle() { return false; } } }));
vi.mock("../src/adapters/boss/boss-adapter", () => ({ BossAdapter: class { canHandle() { return true; } } }));
vi.mock("../src/content/page-controller", () => ({ PageController: class {
  start() { state.start(); return state.dispose; }
  stopCatchup() { state.stop(); }
} }));
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); vi.resetModules(); });

it("starts only when signed in, disposes on logout, and resumes on login without refresh", async () => {
  let token: string | undefined;
  let changed!: (changes: object, area: string) => void;
  vi.stubGlobal("chrome", {
    storage: {
      local: { get: vi.fn(async () => ({ accessToken: token })) },
      onChanged: { addListener: (callback: typeof changed) => { changed = callback; } },
    },
    runtime: { onMessage: { addListener: vi.fn() } },
  });
  await import("../src/content/content-entry");
  await Promise.resolve();
  expect(state.start).not.toHaveBeenCalled();
  token = "test-session";
  changed({ accessToken: {} }, "local");
  await Promise.resolve();
  expect(state.start).toHaveBeenCalledTimes(1);
  token = undefined;
  changed({ accessToken: {} }, "local");
  await Promise.resolve();
  expect(state.stop).toHaveBeenCalledTimes(1);
  expect(state.dispose).toHaveBeenCalledTimes(1);
  token = "next-session";
  changed({ accessToken: {} }, "local");
  await Promise.resolve();
  expect(state.start).toHaveBeenCalledTimes(2);
});
