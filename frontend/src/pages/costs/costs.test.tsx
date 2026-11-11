import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { META, makeMe, mockApi, renderRoute } from "@/test/render";

vi.mock("@/components/chart/echart", () => ({
  EChart: ({ label }: { label: string }) => <figure aria-label={label} />,
  chartColors: () => ({}),
}));

const SUMMARY = {
  currency: "USD", metric: "effective", data_through: "2026-11-14", month_to_date: 2270,
  previous_month_to_date: 2000, change_vs_previous_month_pct: 13.5, last_month: 4200, latest_day: 150,
  forecast: { amount: 4400, low: 4300, high: 4550, daily_rate: 125, recurring_expected: 90,
    change_vs_last_month_pct: 4.8, method: "x" },
  other_currencies: { EUR: 40 },
};
const DAILY = { currency: "USD", periods: ["2026-11-13", "2026-11-14"], total: 300,
  series: [{ key: "EC2", label: "EC2", total: 200, values: [100, 100] },
           { key: "Other", label: "Other (3)", total: 100, values: [50, 50] }] };
const MONTHLY = { currency: "USD", periods: ["2026-10", "2026-11"], total: 6470,
  series: [{ key: "Total", label: "Total", total: 6470, values: [4200, 2270] }] };
const MOVERS = { currency: "USD", current_period: ["2026-11-08", "2026-11-14"], previous_period: ["2026-11-01", "2026-11-07"],
  items: [{ key: "Redshift", current: 210, previous: 0, change: 210, change_pct: null, status: "new" },
          { key: "EC2", current: 600, previous: 700, change: -100, change_pct: -14.3, status: "changed" }] };

const ANOMALY = { id: "a1", day: "2026-11-12", last_day: "2026-11-14", days: 3, peak_impact: 300, label: "Amazon Redshift",
  dimension: "service", key: "Amazon Redshift", currency: "USD",
  actual: 1100, expected: 200, upper: 300, impact: 900, score: 12, status: "open", note: null };

const CHANGE = { id: "c1", occurred_at: "2026-11-13T15:00:00Z", source: "cloudtrail", kind: "infrastructure",
  title: "ResizeCluster warehouse", description: null, service: "Amazon Redshift", account: "2", environment: null,
  url: null, actor: "data-eng",
  effect: { scope: "Amazon Redshift", before_per_day: 200, after_per_day: 1200, change_per_day: 1000, change_pct: 500, days_after: 1 } };

function routes(extra: Record<string, unknown> = {}) {
  return {
    "GET /api/v1/auth/me": makeMe(),
    "GET /api/v1/costs/summary": SUMMARY,
    "GET /api/v1/costs/daily": DAILY,
    "GET /api/v1/costs/monthly": MONTHLY,
    "GET /api/v1/costs/movers": MOVERS,
    "GET /api/v1/meta": META,
    "GET /api/v1/costs/anomalies": [ANOMALY],
    "GET /api/v1/costs/anomalies/a1/changes": [CHANGE],
    "GET /api/v1/costs/hourly": { currency: "USD", hours: [], series: [] },
    "GET /api/v1/costs/timeline": { periods: ["2026-11-13", "2026-11-14"], values: [400, 1300], changes: [CHANGE] },
    ...extra,
  };
}

describe("costs page", () => {
  it("shows headline numbers, charts and movers", async () => {
    mockApi(routes());
    renderRoute("/costs");
    expect(await screen.findByText("Forecast for this month")).toBeInTheDocument();
    expect(screen.getByText("+13.5% vs same days last month")).toBeInTheDocument();
    expect(screen.getByText(/likely/)).toHaveTextContent("$4,300");
    expect(await screen.findByRole("figure", { name: /Daily spend, total \$300/ })).toBeInTheDocument();
    const movers = screen.getByRole("table");
    expect(within(movers).getByText("Redshift")).toBeInTheDocument();
    expect(within(movers).getByText("(new)")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("Spend in EUR is not converted yet");
  });

  it("switches metric and grouping", async () => {
    const { calls } = mockApi(routes());
    renderRoute("/costs");
    await screen.findByText("Forecast for this month");
    await userEvent.selectOptions(screen.getByLabelText("Show"), "billed");
    await userEvent.selectOptions(screen.getByLabelText("Group by"), "account");
    await waitFor(() =>
      expect(calls.some((c) => c.path === "/api/v1/costs/daily")).toBe(true),
    );
    const urls = (globalThis.fetch as unknown as { mock: { calls: [string][] } }).mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes("/api/v1/costs/daily") && u.includes("metric=billed") && u.includes("dimension=account"))).toBe(true);
  });

  it("offers a table instead of the chart", async () => {
    mockApi(routes());
    renderRoute("/costs");
    const [toggle] = await screen.findAllByRole("button", { name: "Show table" });
    await userEvent.click(toggle!);
    expect(screen.getByRole("cell", { name: "Other (3)" })).toBeInTheDocument();
  });

  it("explains how to get data when there is none", async () => {
    mockApi(routes({ "GET /api/v1/costs/summary": { ...SUMMARY, data_through: null, other_currencies: {} } }));
    renderRoute("/costs");
    expect(await screen.findByText("No cost data yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect a cloud account" })).toHaveAttribute("href", "/settings?tab=clouds");
  });

  it("shows headline tiles on the overview", async () => {
    mockApi(routes());
    renderRoute("/");
    expect(await screen.findByText("Month to date")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See where the money goes" })).toHaveAttribute("href", "/costs");
  });
});

describe("anomalies", () => {
  it("lists unusual increases and marks them expected", async () => {
    const { calls } = mockApi(routes({ "PATCH /api/v1/costs/anomalies/a1": { ...ANOMALY, status: "expected" } }));
    renderRoute("/costs");
    expect(await screen.findByText("Amazon Redshift")).toBeInTheDocument();
    expect(screen.getByText("1 open")).toBeInTheDocument();
    expect(screen.getByText("+$900")).toBeInTheDocument();
    expect(screen.getByText(/3 days/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Expected" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "expected" }));
  });

  it("explains an anomaly in plain words on request", async () => {
    mockApi(routes({ "GET /api/v1/costs/anomalies/a1/explanation": {
      text: "Amazon Redshift has cost more than usual. Most of the increase came from account 2.",
      worded_by: "template", changes: [],
      drivers: [{ by: "usage_type", value: "RA3:4xlarge", per_day: 900, usual_per_day: 0, increase_per_day: 900, new: true }],
    } }));
    renderRoute("/costs");
    await userEvent.click(await screen.findByRole("button", { name: "Why did this happen?" }));
    expect(await screen.findByText(/Most of the increase came from account 2/)).toBeInTheDocument();
    expect(screen.getByText("RA3:4xlarge")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide explanation" })).toHaveAttribute("aria-expanded", "true");
  });
});

describe("change timeline", () => {
  it("shows changes with their cost effect and links them to anomalies", async () => {
    mockApi(routes({ "GET /api/v1/auth/me": makeMe({ role: "member" }) }));
    renderRoute("/costs");
    expect(await screen.findByText("What changed")).toBeInTheDocument();
    expect(await screen.findByText(/\$200\/day before, \$1,200\/day after/)).toBeInTheDocument();
    expect(await screen.findByText(/Possible cause:/)).toHaveTextContent("ResizeCluster warehouse");
  });

  it("records a change", async () => {
    const { calls } = mockApi(routes({ "GET /api/v1/auth/me": makeMe({ role: "member" }), "POST /api/v1/changes": CHANGE }));
    renderRoute("/costs");
    await userEvent.click(await screen.findByRole("button", { name: /Record a change/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("What changed"), "Migrated search to Graviton");
    await userEvent.selectOptions(within(dialog).getByLabelText("Type"), "infrastructure");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/v1/changes")?.body).toEqual({
      title: "Migrated search to Graviton", kind: "infrastructure" }));
  });
});

describe("display currency", () => {
  it("remembers the chosen currency and asks the API for it", async () => {
    mockApi(routes());
    renderRoute("/costs");
    await screen.findByText("Forecast for this month");
    await userEvent.selectOptions(screen.getByLabelText("Currency"), "INR");
    await waitFor(() => {
      const urls = (globalThis.fetch as unknown as { mock: { calls: [string][] } }).mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("/api/v1/costs/summary") && u.includes("currency=INR"))).toBe(true);
    });
    expect(localStorage.getItem("ccradar-display-currency")).toBe("INR");
  });

  it("puts every cloud and service side by side", async () => {
    mockApi(routes({
      "GET /api/v1/costs/providers": {
        currency: "USD", metric: "effective", data_through: "2026-11-14", month_to_date: 1625,
        other_currencies: {},
        providers: [
          { provider: "aws", name: "AWS", month_to_date: 1000, previous_month_to_date: 900, change_pct: 11.1, last_month: 2900,
            share_pct: 61.5, data_through: "2026-11-14", connections: [{ id: "c1", name: "Prod", status: "active", error: null }] },
          { provider: "snowflake", name: "Snowflake", month_to_date: 625, previous_month_to_date: 600, change_pct: 4.2, last_month: 1800,
            share_pct: 38.5, data_through: "2026-11-13", connections: [] },
          { provider: "gcp", name: "Google Cloud", month_to_date: 0, previous_month_to_date: 0, change_pct: null, last_month: 0,
            share_pct: 0, data_through: null, connections: [{ id: "c2", name: "GCP", status: "error", error: "denied" }] },
        ],
      },
    }));
    renderRoute("/costs");
    expect(await screen.findByText("All clouds and services")).toBeInTheDocument();
    expect(screen.getByLabelText("AWS: 61.5% of spend")).toHaveAttribute("value", "61.5");
    expect(screen.getByText("Snowflake")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Connection problem/ })).toHaveAttribute("href", "/settings?tab=clouds");
    expect(screen.getByRole("option", { name: "Cloud or service" })).toBeInTheDocument();
  });
});
