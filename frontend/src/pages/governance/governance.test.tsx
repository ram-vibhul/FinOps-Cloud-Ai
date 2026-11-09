import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

const BUDGET = {
  id: "b1", parent_id: null, name: "Account 111", amount: 4000, currency: "USD", period: "monthly", metric: "effective",
  filters: { account: ["111"] }, thresholds: [80, 100], alert_on_forecast: true, recipients: [], period_key: "2026-11",
  period_start: "2026-11-01", period_end: "2026-11-30", spent: 2280, forecast: 3600, spent_pct: 57, forecast_pct: 90,
  children: ["b2"], allocated_to_children: 2500, over_allocated: false,
};
const CHILD = { ...BUDGET, id: "b2", parent_id: "b1", name: "Payments EC2", amount: 2500, spent: 1900, forecast: 3000,
  spent_pct: 76, forecast_pct: 120, children: [], allocated_to_children: 0 };

describe("governance budgets", () => {
  it("shows the budget tree and adds a nested budget", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/budgets": [BUDGET, CHILD],
      "POST /api/v1/budgets": CHILD,
    });
    renderRoute("/governance");
    expect(await screen.findByText("Payments EC2")).toBeInTheDocument();
    expect(screen.getByText("forecast over")).toBeInTheDocument();
    expect(screen.getByText("76% spent, forecast 120%")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Add budget/ }));
    await userEvent.type(screen.getByLabelText("Name"), "Data team");
    await userEvent.type(screen.getByLabelText(/Amount/), "900");
    await userEvent.selectOptions(screen.getByLabelText("Part of"), "b1");
    await userEvent.selectOptions(screen.getByLabelText("Filter 1 on"), "tag");
    await userEvent.type(screen.getByLabelText("Filter 1 key"), "team");
    await userEvent.type(screen.getByLabelText("Filter 1 values"), "data, analytics");
    await userEvent.type(screen.getByLabelText(/Email alerts to/), "lead@acme.com");
    await userEvent.click(screen.getByRole("button", { name: "Save budget" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
      name: "Data team", amount: 900, parent_id: "b1", filters: { "tag:team": ["data", "analytics"] }, recipients: ["lead@acme.com"] }));
  });
});

describe("governance policies", () => {
  it("adds a policy and exempts a violation", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("SAP needs it");
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/governance/policies": [{ id: "p1", name: "No x1e", kind: "denied_instance_types", kind_label: "Denied instance types",
        params: { patterns: ["x1e.*"] }, resource_types: [], severity: "warning", enabled: true, open: 1, monthly_cost: 1216 }],
      "GET /api/v1/governance/violations": [{ id: "v1", policy_id: "p1", policy: "No x1e", severity: "warning", resource: "i-bad",
        resource_type: "ec2_instance", name: "old", account_id: "111", region: "us-west-2",
        detail: "x1e.32xlarge matches the denied pattern x1e.*", monthly_cost: 1216, status: "open", note: null }],
      "POST /api/v1/governance/policies": { id: "p2", name: "Owner tag", kind: "required_tags", kind_label: "Required tags",
        params: {}, resource_types: [], severity: "warning", enabled: true, open: 3, monthly_cost: 0 },
      "POST /api/v1/governance/violations/v1/exempt": { status: "exempt" },
    });
    renderRoute("/governance?tab=policies");
    expect(await screen.findByText("x1e.32xlarge matches the denied pattern x1e.*")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Exempt" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/exempt"))?.body).toEqual({ note: "SAP needs it" }));
    await userEvent.click(screen.getByRole("button", { name: /Add policy/ }));
    await userEvent.type(screen.getByLabelText("Name"), "Owner tag");
    await userEvent.type(screen.getByLabelText(/Values/), "owner, team");
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/v1/governance/policies" && c.method === "POST")?.body).toEqual({
      name: "Owner tag", kind: "required_tags", params: { tags: ["owner", "team"] }, severity: "warning" }));
  });
});

describe("allocation", () => {
  it("shows the chargeback and adds a fixed split", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/allocation/rules": [],
      "GET /api/v1/allocation/showback": { month: "2026-10", dimension: "tag:team", currency: "USD", total: 550.1, allocated_total: 550.1,
        adds_up: true, rules: [], rows: [{ value: "payments", direct: 300, shared: { Support: 52.58 }, shared_total: 52.58, total: 352.58 }] },
      "POST /api/v1/allocation/rules": { id: "r1" },
    });
    renderRoute("/governance?tab=allocation");
    expect(await screen.findByText("payments")).toBeInTheDocument();
    expect(screen.getByText("payments").closest("tr")).toHaveTextContent("$300");
    await userEvent.click(screen.getByRole("button", { name: /Add rule/ }));
    await userEvent.type(screen.getByLabelText("Name"), "Platform");
    await userEvent.type(screen.getByLabelText("is one of"), "Amazon CloudWatch");
    await userEvent.selectOptions(screen.getByLabelText("Split"), "fixed");
    await userEvent.type(screen.getByLabelText(/Percentages/), "payments=60, search=40");
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "Platform", position: 100, dimension: "tag:team", method: "fixed", filters: { service: ["Amazon CloudWatch"] },
      targets: [], percents: { payments: 60, search: 40 } }));
  });
});

describe("statements", () => {
  it("shows reconciliation and closes the month", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("Signed off");
    let closed: { at: string; note: string } | null = null;
    const statement = () => ({ month: "2026-10", currency: "USD", total: 1020, by_category: [{ key: "Usage", cost: 1000 }, { key: "Tax", cost: 20 }],
      by_account: [{ key: "111", cost: 1020 }], by_service: [{ key: "Amazon EC2", cost: 1000 }],
      invoices: [{ id: "i1", invoice_id: "INV-1", provider: "aws", billing_account_id: "999", amount: 1100, currency: "USD",
        billed: 1020, difference: 80, status: "mismatch", source: "manual" }], closed });
    const d = new Date();
    d.setDate(1);
    d.setMonth(d.getMonth() - 1);
    const month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;  // the tab opens on last month
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      [`GET /api/v1/statements/${month}`]: () => statement(),
      [`POST /api/v1/statements/${month}/close`]: () => { closed = { at: "2026-11-09T10:00:00Z", note: "Signed off" }; return statement(); },
    });
    renderRoute("/governance?tab=statements");
    expect(await screen.findByText("mismatch")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /FOCUS export/ })).toHaveAttribute("href", `/api/v1/statements/${month}/focus.csv`);
    await userEvent.click(screen.getByRole("button", { name: /Close month/ }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ note: "Signed off" }));
    expect(await screen.findByText("closed")).toBeInTheDocument();
  });
});

describe("reports", () => {
  it("schedules a weekly report", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/reports/schedules": [],
      "POST /api/v1/reports/schedules": { id: "s1" },
    });
    renderRoute("/governance?tab=reports");
    expect(await screen.findByRole("link", { name: /Cost summary/ })).toHaveAttribute("href", "/api/v1/reports/download?kind=cost_summary");
    expect(screen.getByRole("link", { name: /Leadership one-pager/ })).toHaveAttribute("href", "/api/v1/reports/download?kind=leadership");
    await userEvent.click(screen.getByRole("button", { name: /Schedule a report/ }));
    await userEvent.type(screen.getByLabelText("Name"), "Weekly savings");
    await userEvent.selectOptions(screen.getByLabelText("Report"), "savings");
    await userEvent.selectOptions(screen.getByLabelText("How often"), "weekly");
    await userEvent.selectOptions(screen.getByLabelText("On"), "4");
    await userEvent.type(screen.getByLabelText(/Send to/), "team@acme.com");
    await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "Weekly savings", kind: "savings",
      frequency: "weekly", weekday: 4, day: 1, format: "pdf", recipients: ["team@acme.com"], params: {} }));
  });
});

describe("unit costs", () => {
  it("shows cost per unit for the first metric", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/unit-metrics": [{ id: "m1", name: "Orders", unit: "order", description: "", filters: {}, metric: "effective", target_per_unit: 0.05 }],
      "GET /api/v1/unit-metrics/m1/report": { metric: { id: "m1", name: "Orders", unit: "order", target_per_unit: 0.05 }, currency: "USD",
        per_unit: 0.0588, change_pct: -58.3, days_with_units: 10, days: [], months: [{ month: "2026-10", cost: 1000, units: 17000, per_unit: 0.0588 }] },
    });
    renderRoute("/governance?tab=unit-costs");
    expect(await screen.findByText("0.0588 USD", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText(/-58.3% recently/)).toBeInTheDocument();
    expect(screen.getByText("17,000")).toBeInTheDocument();
  });
});
