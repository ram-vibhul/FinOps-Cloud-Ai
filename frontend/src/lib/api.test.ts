import { ApiError, apiFetch } from "@/lib/api";
import { mockFetchJson } from "@/test/render";

describe("apiFetch", () => {
  it("returns parsed JSON", async () => {
    mockFetchJson({ ok: 1 });
    await expect(apiFetch("/api/x")).resolves.toEqual({ ok: 1 });
  });

  it("throws ApiError from a problem+json body", async () => {
    mockFetchJson(
      { type: "about:blank", title: "Forbidden", status: 403, code: "forbidden", detail: "Nope", request_id: "r1" },
      403,
    );
    const err = await apiFetch("/api/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(403);
    expect((err as ApiError).message).toBe("Nope");
    expect((err as ApiError).code).toBe("forbidden");
    expect((err as ApiError).requestId).toBe("r1");
  });

  it("falls back to the title, then the status text", async () => {
    mockFetchJson({ type: "about:blank", title: "Not Found", status: 404, code: "not_found" }, 404);
    await expect(apiFetch("/api/x")).rejects.toThrow("Not Found");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("<html>bad gateway</html>", { status: 502 }));
    const err = (await apiFetch("/api/x").catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(502);
    expect(err.code).toBe("http_error");
  });

  it("sends JSON content type when there is a body", async () => {
    const spy = mockFetchJson({});
    await apiFetch("/api/x", { method: "POST", body: "{}" });
    const init = spy.mock.calls[0]?.[1];
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(init?.credentials).toBe("same-origin");
  });
});
