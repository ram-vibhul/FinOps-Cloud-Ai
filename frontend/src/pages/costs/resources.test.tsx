import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

vi.mock("@/components/chart/echart", () => ({
  EChart: ({ label }: { label: string }) => <figure aria-label={label} />,
  chartColors: () => ({}),
}));

const VOL = { id: "r1", provider: "aws", account_id: "222222222222", region: "ap-south-1", service: "Amazon Elastic Compute Cloud",
  resource_type: "ebs_volume", resource_id: "vol-1", name: "backup", state: "available", owner: null, age_days: 160,
  cost_30d: 50, cost_source: "billing export", tags: { team: "data" }, attributes: { size_gb: 500, attached_to: [] } };

describe("resources", () => {
  it("lists resources, filters by type and opens details", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/resources": { currency: "USD", total: 1, by_type: { ebs_volume: 1, ec2_instance: 4 }, items: [VOL] },
      "GET /api/v1/resources/r1": { resource: VOL, currency: "USD", daily: [{ day: "2026-11-10", cost: 1.6 }] },
    });
    renderRoute("/costs/resources");
    expect(await screen.findByText("backup")).toBeInTheDocument();
    expect(screen.getByText("no owner")).toBeInTheDocument();
    expect(screen.getByText("160 d")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Volumes (1)" }));
    await waitFor(() => {
      const urls = (globalThis.fetch as unknown as { mock: { calls: [string][] } }).mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("resource_type=ebs_volume"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: /backup/ }));
    expect(await screen.findByText("size_gb")).toBeInTheDocument();
    expect(screen.getByText("tag:team")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/api/v1/resources/r1")).toBe(true);
  });
});
