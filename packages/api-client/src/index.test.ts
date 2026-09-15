import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "./index";

afterEach(() => vi.restoreAllMocks());

describe("ApiClient authentication recovery", () => {
  it("refreshes an expired access token once and retries the original request", async () => {
    let token = "expired-token";
    const controller = new AbortController();
    const refresh = vi.fn(async (signal?: AbortSignal) => {
      expect(signal).toBe(controller.signal);
      token = "fresh-token";
      return token;
    });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { code: "TOKEN_EXPIRED" } }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "ok" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    const client = new ApiClient("http://api.test", () => token, refresh);

    await expect(
      client.request<{ status: string }>("/admin/operations", { signal: controller.signal }),
    ).resolves.toEqual({ status: "ok" });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(
      new Headers(fetchMock.mock.calls[1][1]?.headers).get("Authorization"),
    ).toBe("Bearer fresh-token");
  });

  it("clears the session when refresh cannot recover authentication", async () => {
    const failed = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "TOKEN_EXPIRED" } }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ApiClient(
      "http://api.test",
      () => "expired-token",
      async () => null,
      failed,
    );

    await expect(client.request("/admin/operations")).rejects.toMatchObject({
      status: 401,
    });
    expect(failed).toHaveBeenCalled();
  });

  it("allows an aborted request to cancel token refresh", async () => {
    const controller = new AbortController();
    const refresh = vi.fn(
      (signal?: AbortSignal) =>
        new Promise<string | null>((_, reject) => {
          if (signal?.aborted) {
            reject(signal.reason ?? new DOMException("aborted", "AbortError"));
            return;
          }
          signal?.addEventListener(
            "abort",
            () => reject(signal.reason ?? new DOMException("aborted", "AbortError")),
            { once: true },
          );
        }),
    );
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "TOKEN_EXPIRED" } }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ApiClient("http://api.test", () => "expired-token", refresh);
    const request = client.request("/admin/operations", { signal: controller.signal });

    controller.abort();

    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(refresh).toHaveBeenCalledWith(controller.signal);
  });

  it("lets the runtime set the multipart boundary for FormData", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ApiClient("http://api.test", () => "token");
    const form = new FormData();
    form.append("file", new Blob(["resume"]), "resume.txt");

    await client.request("/upload", { method: "POST", body: form });

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer token");
    expect(headers.has("Content-Type")).toBe(false);
  });
});
