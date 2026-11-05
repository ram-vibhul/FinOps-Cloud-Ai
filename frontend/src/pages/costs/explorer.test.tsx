import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

vi.mock("@/components/chart/echart", () => ({
  EChart: ({ label }: { label: string }) => <figure aria-label={label} />,
  chartColors: () => ({}),
}));

const RESULT = {
  currency: "USD", metric: "effective", group_by: ["service"], source: "daily", total: 220, group_count: 2,
  periods: ["2026-11-01"], series: [{ key: "EC2", label: "EC2", total: 160, values: [160] }],
  groups: [
    { key: ["EC2"], label: "EC2", total: 160, share: 72.7, previous: 50, change_pct: 220 },
    { key: ["S3"], label: "S3", total: 60, share: 27.3, previous: 0, change_pct: null },
  ],
};

function routes(extra: Record<string, unknown> = {}) {
  return {
    "GET /api/v1/auth/me": makeMe({ role: "member" }),
    "GET /api/v1/costs/tags": [{ key: "team", tagged_cost: 100, coverage_pct: 45 }, { key: "env", tagged_cost: 80, coverage_pct: 30 }],
    "GET /api/v1/org/dimension-settings": { team_tag: "team", project_tag: "project" },
    "GET /api/v1/virtual-tags": [{ id: "vt1", key: "team", rules: [] }],
    "GET /api/v1/views": [{ id: "v1", name: "Team costs", shared: true, mine: false,
      query: { group_by: ["tag:team"], filters: {}, granularity: "total", days: 90 } }],
    "POST /api/v1/costs/explore": RESULT,
    "GET /api/v1/costs/dimensions/service/values": [{ value: "EC2", total: 160 }, { value: "S3", total: 60 }],
    "POST /api/v1/views": { id: "v2", name: "Mine", shared: false, mine: true, query: {} },
    ...extra,
  };
}

const bodies = (calls: { method: string; path: string; body: unknown }[]) =>
  calls.filter((c) => c.path === "/api/v1/costs/explore").map((c) => c.body as Record<string, unknown>);

describe("cost explorer", () => {
  it("shows groups with share and change", async () => {
    mockApi(routes());
    renderRoute("/costs/explore");
    const table = await screen.findByRole("table");
    expect(within(table).getByText("72.7%")).toBeInTheDocument();
    expect(within(table).getByText("+220.0%")).toBeInTheDocument();
    expect(within(table).getByText("new")).toBeInTheDocument();
  });

  it("groups by a tag and adds a filter", async () => {
    const { calls } = mockApi(routes());
    renderRoute("/costs/explore");
    await screen.findByRole("table");
    const groupBy = screen.getByLabelText("Group by");
    await waitFor(() => expect(within(groupBy).getByRole("option", { name: "Tag: env" })).toBeInTheDocument());
    expect(within(groupBy).queryByRole("option", { name: "Tag: team" })).not.toBeInTheDocument();  // shown as Team
    await userEvent.selectOptions(groupBy, "tag:env");
    await waitFor(() => expect(bodies(calls).at(-1)?.group_by).toEqual(["tag:env"]));
    await userEvent.click(screen.getByRole("button", { name: /Add filter/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: /S3/ }));
    await waitFor(() => expect(bodies(calls).at(-1)?.filters).toEqual({ service: ["S3"] }));
    await userEvent.click(screen.getByRole("button", { name: "Remove filter Service S3" }));
    await waitFor(() => expect(bodies(calls).at(-1)?.filters).toEqual({}));
  });

  it("opens and saves views", async () => {
    const { calls } = mockApi(routes());
    renderRoute("/costs/explore");
    await screen.findByRole("table");
    await userEvent.selectOptions(screen.getByLabelText("Saved views"), "v1");
    await waitFor(() => expect(bodies(calls).at(-1)).toMatchObject({ group_by: ["tag:team"], granularity: "total" }));
    await userEvent.click(screen.getByRole("button", { name: /Save view/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Name"), "Mine");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/views")?.body).toMatchObject({
      name: "Mine", shared: false, query: { group_by: ["tag:team"], days: 90 } }));
  });
});
