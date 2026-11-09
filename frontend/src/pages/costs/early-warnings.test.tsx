import { screen } from "@testing-library/react";
import { makeMe, META, mockApi, renderRoute } from "@/test/render";

const SUMMARY = {
  currency: "USD", metric: "effective", data_through: "2026-11-08", month_to_date: 41200, previous_month_to_date: 38000,
  change_vs_previous_month_pct: 8.4, last_month: 112000, latest_day: 5100,
  forecast: { amount: 128000, low: 120000, high: 136000, daily_rate: 5100, recurring_expected: 0, change_vs_last_month_pct: 14.3, method: "x" },
  other_currencies: {},
};

describe("early warnings", () => {
  it("shows a banner on the overview when spend runs hot", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(), "GET /api/v1/meta": META, "GET /api/v1/costs/summary": SUMMARY, "GET /api/v1/costs/anomalies": [],
      "GET /api/v1/costs/early-warnings": [
        { kind: "forecast", over_pct: 14.3, forecast: 128000, last_month: 112000, drivers: [{ key: "Amazon Redshift", change: 4200 }] },
        { kind: "same_day", over_pct: 120, so_far: 220, usual: 100, hours: 10, drivers: [{ key: "Amazon EC2", change: 120 }] },
      ],
    });
    renderRoute("/");
    const banner = await screen.findByLabelText("Early warnings");
    expect(banner).toHaveTextContent("14.3% above last month");
    expect(banner).toHaveTextContent("Spend today is 120% above normal");
    expect(banner).toHaveTextContent("Driven by Amazon Redshift");
  });
});
