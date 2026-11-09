import { screen } from "@testing-library/react";
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
});
