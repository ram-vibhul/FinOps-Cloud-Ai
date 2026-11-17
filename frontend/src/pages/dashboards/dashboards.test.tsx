import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

const RESULT = {
  currency: "USD", metric: "effective", group_by: ["service"], source: "daily", total: 1234, group_count: 2,
  groups: [{ key: ["EC2"], label: "EC2", total: 1000, share: 81, previous: 900, change_pct: 11 },
    { key: ["S3"], label: "S3", total: 234, share: 19, previous: 200, change_pct: 17 }],
  periods: [], series: [],
};

describe("dashboards", () => {
  it("shows each saved view of a dashboard", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/dashboards": [{ id: "d1", name: "Weekly", shared: true, mine: true, widgets: [
        { view_id: "v1", title: "Top services", chart: "table", query: { group_by: ["service"], filters: {}, granularity: "total", days: 30 } },
      ] }],
      "POST /api/v1/costs/explore": RESULT,
    });
    renderRoute("/dashboards");
    const table = await screen.findByRole("table", { name: "Top services" });
    expect(within(table).getByText("EC2")).toBeInTheDocument();
    expect(screen.getByText("Total $1,234")).toBeInTheDocument();
  });

  it("creates a dashboard from saved views", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "member" }),
      "GET /api/v1/dashboards": [],
      "GET /api/v1/views": [{ id: "v1", name: "By service", shared: true, mine: true, query: { group_by: ["service"], filters: {}, granularity: "day" } }],
      "POST /api/v1/dashboards": { id: "d2", name: "Mine", shared: true, mine: true, widgets: [] },
    });
    renderRoute("/dashboards");
    expect(await screen.findByText("No dashboards yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /New dashboard/ }));
    const form = await screen.findByRole("form", { name: "New dashboard" });
    await userEvent.type(within(form).getByLabelText("Name"), "Mine");
    await userEvent.click(within(form).getByLabelText("By service"));
    await userEvent.selectOptions(within(form).getByLabelText("Chart for By service"), "line");
    await userEvent.click(within(form).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/dashboards")?.body)
      .toEqual({ name: "Mine", shared: true, widgets: [{ view_id: "v1", chart: "line" }] }));
  });
});
