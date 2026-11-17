import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

describe("why did cost change", () => {
  it("shows what grew, the change behind it and a fix", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "POST /api/v1/costs/investigate": {
        currency: "USD", start: "2026-11-08", end: "2026-11-14", now: 385, before: 105, change: 280, top_service: "EC2",
        summary: "Spend rose by $280, mostly EC2.", worded_by_ai: false,
        drivers: [{ dimension: "service", key: "EC2", label: "EC2", now: 350, before: 70, change: 280 }],
        within: { resource: [{ dimension: "resource", key: "i-big", label: "i-big", now: 280, before: 0, change: 280 }] },
        changes: [{ id: "c1", occurred_at: "2026-11-08T09:00:00Z", title: "RunInstances i-big", actor: "ravi", source: "cloudtrail", monthly: 1216 }],
        anomalies: [],
        fixes: [{ id: "f1", title: "Right-size i-big", savings_monthly: 600, detector: "aws.ec2_rightsizing", resource_id: "i-big" }],
      },
    });
    renderRoute("/costs");
    await userEvent.click(await screen.findByRole("button", { name: /Investigate/ }));
    expect(await screen.findByText("Spend rose by $280, mostly EC2.")).toBeInTheDocument();
    const grew = screen.getByRole("table", { name: "What grew" });
    expect(within(grew).getAllByText("+$280")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "RunInstances i-big" })).toHaveAttribute("href", "/radar?change=c1");
    expect(screen.getByText(/saves \$600 a month/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/v1/costs/investigate")?.body).toEqual({ days: 7 });
  });
});
