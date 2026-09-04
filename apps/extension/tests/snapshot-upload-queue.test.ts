import { describe, expect, it } from "vitest";
import { shouldRetrySnapshot } from "../src/background/snapshot-upload-queue";

describe("snapshot upload retry policy", () => {
  it("retries network, auth and server failures", () => {
    expect(shouldRetrySnapshot(new Error("network"))).toBe(true);
    expect(shouldRetrySnapshot(Object.assign(new Error(), { status: 401 }))).toBe(true);
    expect(shouldRetrySnapshot(Object.assign(new Error(), { status: 503 }))).toBe(true);
  });

  it("drops stale or invalid source failures", () => {
    expect(shouldRetrySnapshot(Object.assign(new Error(), { status: 404 }))).toBe(false);
    expect(shouldRetrySnapshot(Object.assign(new Error(), { status: 422 }))).toBe(false);
  });
});
