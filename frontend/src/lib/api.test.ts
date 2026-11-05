import { ApiError, apiFetch } from "@/lib/api";
import { mockFetchJson } from "@/test/render";

describe("apiFetch", () => {
  it("returns parsed JSON", async () => {
    mockFetchJson({ ok: 1 });
    await expect(apiFetch("/api/x")).resolves.toEqual({ ok: 1 });
  });

  it("throws ApiError with the server detail", async () => {
    mockFetchJson({ detail: "Nope" }, 403);
    const err = await apiFetch("/api/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(403);
    expect((err as ApiError).message).toBe("Nope");
  });

  it("sends JSON content type when there is a body", async () => {
    const spy = mockFetchJson({});
    await apiFetch("/api/x", { method: "POST", body: "{}" });
    const init = spy.mock.calls[0]?.[1];
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(init?.credentials).toBe("same-origin");
  });
});
