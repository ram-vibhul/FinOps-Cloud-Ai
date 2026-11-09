import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

const SUMMARY = {
  start: "2026-10-11", end: "2026-11-09",
  cloud_services: [{ provider: "aws", service: "Amazon Bedrock", currency: "USD", cost: 412 }],
  sdk: {
    totals: { cost_usd: 96.5, requests: 1200, input_tokens: 4_000_000, output_tokens: 300_000, cache_read_tokens: 9_000_000, retries: 14, unpriced_requests: 3 },
    by_model: [{ key: "anthropic/claude-sonnet-5-5", cost_usd: 90, requests: 1000, input_tokens: 3_800_000, output_tokens: 280_000 }],
    by_app: [], by_team: [{ key: "payments", cost_usd: 96.5, requests: 1200, input_tokens: 4_000_000, output_tokens: 300_000 }],
    by_feature: [{ key: "emails", cost_usd: 60, requests: 700, input_tokens: 2_000_000, output_tokens: 100_000 }],
  },
};

describe("AI spend", () => {
  it("shows bill services, SDK usage and creates a key once", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "admin" }),
      "GET /api/v1/ai/summary": SUMMARY,
      "GET /api/v1/org/ai-usage-keys": [],
      "GET /api/v1/ai/changes": { days: 7, now: 40, before: 20, change: 20, sentences: ["AI spend reported by your applications was 40.00 USD in the last 7 days, up 20.00 USD from 20.00 USD."] },
      "GET /api/v1/ai/waste": [{ kind: "repeated_prompts", key: "support/triage", title: "Cache repeated prompts in support/triage", detail: "3 prompt(s) were sent 900 times", saving_monthly: 81, requests: 900 }],
      "POST /api/v1/org/ai-usage-keys": { id: "k1", name: "checkout", key_hint: "abc123", last_used_at: null, created_at: "", key: "clai_secret_abc123" },
    });
    renderRoute("/costs/ai");
    expect(await screen.findByLabelText("AI services on cloud bills")).toHaveTextContent("Amazon Bedrock");
    expect(screen.getByText("14 retried request(s)")).toBeInTheDocument();
    expect(await screen.findByLabelText("What changed")).toHaveTextContent("up 20.00 USD");
    expect(screen.getByLabelText("Wasted tokens")).toHaveTextContent("Cache repeated prompts in support/triage");
    expect(screen.getByText("3 request(s) for models without a price")).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "By model" })).getByText("anthropic/claude-sonnet-5-5")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Key name"), "checkout");
    await userEvent.click(screen.getByRole("button", { name: "Create key" }));
    expect(await screen.findByLabelText("New SDK key")).toHaveTextContent("clai_secret_abc123");
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "checkout" }));
  });

  it("hides key management from members", async () => {
    mockApi({ "GET /api/v1/auth/me": makeMe({ role: "member" }), "GET /api/v1/ai/summary": SUMMARY });
    renderRoute("/costs/ai");
    await screen.findByLabelText("AI services on cloud bills");
    expect(screen.queryByText("SDK keys")).not.toBeInTheDocument();
  });
});
