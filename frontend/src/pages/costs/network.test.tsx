import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

vi.mock("@/components/chart/echart", () => ({
  EChart: ({ label }: { label: string }) => <figure aria-label={label} />,
  chartColors: () => ({}),
}));

describe("network costs", () => {
  it("shows totals per kind, the daily chart and who pays", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/costs/network": {
        currency: "USD", days: 30, total: 4890,
        categories: [{ key: "nat_processing", label: "NAT gateway processing", cost: 2850, share_pct: 58.3 },
          { key: "cross_az", label: "Between availability zones", cost: 1140, share_pct: 23.3 }],
        daily: { periods: ["2026-11-06", "2026-11-07"], series: [
          { key: "nat_processing", label: "NAT gateway processing", total: 190, values: [95, 95] }] },
        top: [{ category: "nat_processing", label: "NAT gateway processing", resource_id: "nat-0shared00000001",
          service: "Amazon Virtual Private Cloud", account: "222222222222", region: "ap-south-1", cost: 2850 }],
      },
      "GET /api/v1/costs/network/attribution": {
        computed_at: "2026-11-08T04:30:00Z", currency: "USD",
        categories: { nat_processing: "NAT gateway processing", cross_az: "Between availability zones" },
        items: [
          { category: "nat_processing", owner: "search/indexer (prod)", owner_kind: "workload", team: "search", gb: 812.5,
            share_pct: 71.2, cost_monthly: 2029.2, az: "ap-south-1a" },
          { category: "cross_az", owner: "checkout-web-2", owner_kind: "instance", team: "payments", gb: 90.1,
            share_pct: 40, cost_monthly: 456, az: "ap-south-1b" },
        ],
        teams: [{ team: "search", cost_monthly: 2029.2 }, { team: "payments", cost_monthly: 456 }],
      },
    });
    renderRoute("/costs/network");
    expect(await screen.findByText("(58.3%)")).toBeInTheDocument();
    expect(screen.getByRole("figure", { name: /Daily spend/ })).toBeInTheDocument();
    expect(screen.getByText("nat-0shared00000001")).toBeInTheDocument();
    expect(await screen.findByText("search/indexer (prod)")).toBeInTheDocument();
    expect(screen.getByLabelText("By team")).toHaveTextContent("search: $2,029 · payments: $456");
    await userEvent.selectOptions(screen.getByLabelText("Kind of transfer"), "cross_az");
    expect(screen.queryByText("search/indexer (prod)")).not.toBeInTheDocument();
  });
});
