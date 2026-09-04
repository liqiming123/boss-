import { describe, expect, it } from "vitest";
import { shouldRetryMessageSent } from "../src/background/message-sent-queue";

describe("message sent retry policy", () => {
  it("retries network, authentication and server failures", () => {
    expect(shouldRetryMessageSent(new Error("network"))).toBe(true);
    expect(
      shouldRetryMessageSent(
        Object.assign(new Error("unauthorized"), { status: 401 }),
      ),
    ).toBe(true);
    expect(
      shouldRetryMessageSent(
        Object.assign(new Error("server"), { status: 503 }),
      ),
    ).toBe(true);
  });
  it("does not retry invalid business payloads forever", () => {
    expect(
      shouldRetryMessageSent(
        Object.assign(new Error("bad request"), { status: 400 }),
      ),
    ).toBe(false);
  });
});
