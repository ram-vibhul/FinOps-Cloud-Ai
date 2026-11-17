import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

describe("executive summary", () => {
  it("shows the headline numbers", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/roi/executive": { currency: "USD", as_of: "2026-11-08",
        spend: { month_to_date: 41200, forecast: 118000, last_month: 112000, change_vs_last_month_pct: 5.4 },
        movers: [{ key: "Amazon Redshift", current: 9000, previous: 4000, change: 5000 }],
        budgets: { count: 4, at_risk: [{ name: "Data team", forecast_pct: 112 }] },
        savings: { open_monthly: 9508, open_count: 31, this_week_monthly: 2400, kept_latest_month: 1240, kept_month: "2026-10" },
        commitments: { coverage_pct: 58, utilization_pct: 96.4 },
        unit_cost: { name: "Orders", unit: "order", per_unit: 0.0588, change_pct: -4.2 }, policy_violations: 7, anomalies_this_month: 2 },
      "GET /api/v1/reports/leadership": { month: "2026-10", month_label: "October 2026", currency: "USD", closed: true, total: 112000,
        previous_total: 104000, change_pct: 7.7, sentences: ["Cloud spend in October 2026 was 112,000 USD, up 8% from 104,000 USD the month before."] },
    });
    renderRoute("/executive");
    expect(await screen.findByText("1 of 4")).toBeInTheDocument();
    expect(screen.getByText(/Data team 112%/)).toBeInTheDocument();
    expect(screen.getByText("Cost per order")).toBeInTheDocument();
    expect(screen.getByLabelText("Biggest changes")).toHaveTextContent("Amazon Redshift");
    expect(await screen.findByText("October 2026 on one page")).toBeInTheDocument();
    expect(screen.getByLabelText("Leadership report")).toHaveTextContent("up 8%");
    expect(screen.getByRole("link", { name: /Download PDF/ })).toHaveAttribute("href", "/api/v1/reports/download?kind=leadership");
  });

  it("shows estimated carbon by month and region", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/carbon": {
        unit: "kg CO2e", total_kg: 12500, measured_share: 62.5, method: "Cloud Carbon Footprint coefficients.",
        months: [{ month: "2026-10", from_usage: 5000, from_spend: 2500 }, { month: "2026-11", from_usage: 2800, from_spend: 2200 }],
        by_service: [], by_region: [{ name: "ap-south-1", kg: 9000 }],
      },
    });
    renderRoute("/executive");
    const months = await screen.findByRole("table", { name: "Carbon by month" });
    expect(within(months).getByText("2026-10")).toBeInTheDocument();
    expect(within(months).getByText("5 t")).toBeInTheDocument();
    expect(screen.getByText(/About 12.5 t CO2e. 62.5% worked out from what was used/)).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Carbon by region" })).getByText("ap-south-1")).toBeInTheDocument();
  });

  it("compares with peers once enough share, and opts in", async () => {
    const defs = {
      effective_savings_rate: { label: "Effective savings rate", unit: "%", better: "higher" },
      waste_pct: { label: "Waste (open savings / spend)", unit: "%", better: "lower" },
      allocated_pct: { label: "Spend allocated to a team", unit: "%", better: "higher" },
      vcpu_hour_cost: { label: "EC2 cost per vCPU-hour", unit: "USD", better: "lower" },
    };
    const base = { month: "2026-10-01", size_band: "medium", min_peers: 5, definitions: defs,
      mine: { effective_savings_rate: 22.5, waste_pct: 4, allocated_pct: 81, vcpu_hour_cost: 0.0412 } };
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "admin" }),
      "GET /api/v1/benchmarks": { ...base, opted_in: false, peers: null },
      "PUT /api/v1/benchmarks/opt-in": { ...base, opted_in: true, peers: { scope: "size band", organisations: 7, metrics: {
        effective_savings_rate: { n: 7, p25: 12, median: 18, p75: 27 }, waste_pct: { n: 7, p25: 3, median: 6, p75: 9 },
        allocated_pct: { n: 7, p25: 60, median: 75, p75: 90 }, vcpu_hour_cost: { n: 3 } } } },
    });
    renderRoute("/executive");
    const table = await screen.findByRole("table", { name: "Benchmarks" });
    expect(within(table).getByText("22.5%")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Share my ratios and compare" }));
    expect(calls).toContainEqual(expect.objectContaining({ method: "PUT", path: "/api/v1/benchmarks/opt-in" }));
  });
});
