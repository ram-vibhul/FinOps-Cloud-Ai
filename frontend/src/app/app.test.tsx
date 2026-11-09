import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ANONYMOUS, META, makeMe, mockApi, renderRoute } from "@/test/render";

const EMPTY_SUMMARY = {
  currency: "USD", metric: "effective", data_through: null, month_to_date: 0, previous_month_to_date: 0,
  change_vs_previous_month_pct: null, last_month: 0, latest_day: 0,
  forecast: { amount: 0, low: 0, high: 0, daily_rate: 0, recurring_expected: 0, change_vs_last_month_pct: null, method: "no data" },
  other_currencies: {},
};

describe("app shell", () => {
  it("renders the overview with instance details when signed in", async () => {
    mockApi({ "GET /api/v1/auth/me": makeMe(), "GET /api/v1/meta": META, "GET /api/v1/costs/summary": EMPTY_SUMMARY, "GET /api/v1/costs/anomalies": [] });
    renderRoute("/");
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(await screen.findByText(/CostLens 0.1.0 · self-hosted · test/)).toBeInTheDocument();
  });

  it("shows a banner on every page while CostLens can change resources", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "admin" }), "GET /api/v1/meta": META, "GET /api/v1/costs/summary": EMPTY_SUMMARY, "GET /api/v1/costs/anomalies": [],
      "GET /api/v1/org/write-access": [{ connection_id: "c1", name: "Prod", account_id: "111111111111", enabled_by: "o@acme.com",
        enabled_at: "2026-11-01T00:00:00Z", expires_at: "2026-12-01T00:00:00Z", auto_request: false }],
      "DELETE /api/v1/connections/c1/fixes": null,
    });
    renderRoute("/");
    const banner = await screen.findByLabelText("Write access");
    expect(banner).toHaveTextContent("CostLens can change resources in Prod (111111111111): turned on by o@acme.com");
    await userEvent.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/v1/connections/c1/fixes")).toBe(true));
  });

  it("sends anonymous visitors to sign-in with a return path", async () => {
    mockApi({ "GET /api/v1/auth/me": ANONYMOUS, "GET /api/v1/auth/providers": [] });
    const { router } = renderRoute("/costs?range=30d");
    await waitFor(() => expect(router.state.location.pathname).toBe("/sign-in"));
    expect(router.state.location.search).toBe("?return_to=%2Fcosts%3Frange%3D30d");
  });

  it("sends people without an organisation to the welcome page", async () => {
    mockApi({ "GET /api/v1/auth/me": makeMe({ organizations: [], active_organization_id: null, role: null }) });
    renderRoute("/");
    expect(await screen.findByRole("button", { name: /Create organisation/ })).toBeInTheDocument();
  });

  it("offers a retry when the server is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));
    renderRoute("/");
    expect(await screen.findByText(/Could not reach CostLens/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("navigates between sections", async () => {
    mockApi({ "GET /api/v1/auth/me": makeMe(), "GET /api/v1/meta": META, "GET /api/v1/costs/summary": EMPTY_SUMMARY, "GET /api/v1/costs/anomalies": [] });
    renderRoute("/");
    const nav = (await screen.findAllByRole("navigation", { name: "Main" }))[0]!;
    await userEvent.click(nav.querySelector('a[href="/governance"]')!);
    expect(await screen.findByRole("heading", { level: 1, name: "Governance" })).toBeInTheDocument();
  });

  it("shows not found for unknown paths", async () => {
    mockApi({ "GET /api/v1/auth/me": makeMe() });
    renderRoute("/nope");
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
  });

  it("opens and closes mobile navigation", async () => {
    mockApi({ "GET /api/v1/auth/me": makeMe(), "GET /api/v1/meta": META, "GET /api/v1/costs/summary": EMPTY_SUMMARY, "GET /api/v1/costs/anomalies": [] });
    renderRoute("/");
    await userEvent.click(await screen.findByRole("button", { name: "Open navigation" }));
    expect(screen.getByRole("dialog", { name: "Navigation" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close navigation" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("switches organisation from the header", async () => {
    let active = "o1";
    const me = () =>
      makeMe({
        organizations: [
          { id: "o1", name: "Acme", slug: "acme", role: "owner", is_demo: false },
          { id: "o2", name: "Beta", slug: "beta", role: "viewer", is_demo: false },
        ],
        active_organization_id: active,
      });
    const { calls } = mockApi({
      "GET /api/v1/auth/me": () => me(),
      "GET /api/v1/meta": META, "GET /api/v1/costs/summary": EMPTY_SUMMARY, "GET /api/v1/costs/anomalies": [],
      "POST /api/v1/auth/switch": (init: RequestInit | undefined) => {
        active = JSON.parse(String(init?.body)).organization_id;
        return null;
      },
    });
    renderRoute("/");
    const [switcher] = await screen.findAllByRole("button", { name: "Switch organisation" });
    await userEvent.click(switcher!);
    await userEvent.click(await screen.findByRole("menuitem", { name: /Beta/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/auth/switch")).toBe(true));
    expect(await screen.findAllByText("Beta")).not.toHaveLength(0);
  });

  it("shows the read-only banner in the demo", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe({
        organizations: [{ id: "d", name: "Demo Company", slug: "demo", role: "viewer", is_demo: true }],
        active_organization_id: "d",
        role: "viewer",
      }),
      "GET /api/v1/meta": META, "GET /api/v1/costs/summary": EMPTY_SUMMARY, "GET /api/v1/costs/anomalies": [],
    });
    renderRoute("/");
    expect(await screen.findByText(/exploring the demo organisation/)).toBeInTheDocument();
  });

  it("signs out from the account menu", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/meta": META, "GET /api/v1/costs/summary": EMPTY_SUMMARY, "GET /api/v1/costs/anomalies": [],
      "POST /api/v1/auth/logout": null,
      "GET /api/v1/auth/providers": [],
    });
    const { router } = renderRoute("/");
    await userEvent.click(await screen.findByRole("button", { name: "Account menu" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Sign out/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/sign-in"));
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/auth/logout")).toBe(true);
  });
});
