import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

const FINDING = {
  id: "f1", detector: "k8s.rightsizing", category: "kubernetes", title: "Lower requests for payments-gateway/app in prod/payments",
  description: "Requests are 2000m CPU but the highest use was 280m.", provider: "kubernetes", account_id: null, region: null,
  service: "Kubernetes", resource_type: "Deployment", resource_id: "k1/payments/Deployment/payments-gateway",
  savings_monthly: 186.4, currency: "USD", risk: "low", effort: "low",
  steps: ["Set requests to 340m CPU and 1056Mi memory."], commands: ["kubectl -n payments set resources deployment/payments-gateway -c app --requests=cpu=340m,memory=1056Mi"],
  evidence: {}, status: "open", status_note: null, first_seen_at: "2026-11-05T00:00:00Z", last_seen_at: "2026-11-08T00:00:00Z",
  score: 186.4, overlaps: false,
};

describe("savings", () => {
  it("shows the deduplicated total and a finding with steps and commands", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "member" }),
      "GET /api/v1/findings": { summary: { count: 2, savings_monthly: 190.4, overlapping: 1,
        by_category: { kubernetes: { count: 2, savings_monthly: 190.4 } } }, items: [FINDING], detectors: [] },
      "PATCH /api/v1/findings/f1": { ...FINDING, status: "dismissed" },
      "GET /api/v1/org/members": [{ user_id: "u2", email: "ravi@acme.com", name: "Ravi", avatar_url: null, role: "member", joined_at: "" }],
      "GET /api/v1/findings/f1/history": [{ at: "2026-11-08T10:00:00Z", kind: "comment", text: "Snapshot taken", data: {}, who: "Ravi" }],
      "PUT /api/v1/findings/f1/owner": { ...FINDING, owner_id: "u2" },
      "POST /api/v1/findings/f1/dismiss": { rule_id: "r1", dismissed: 1 },
      "GET /api/v1/findings/suppressions": [], "GET /api/v1/findings/noise": [],
      "GET /api/v1/findings/kept": { currency: "USD", latest_month: "2026-10", kept_latest_month: 1240, kept_total: 3100,
        months: [{ month: "2026-09", kept: 1860 }, { month: "2026-10", kept: 1240 }],
        regressions: [{ id: "f9", title: "Downsize i-back", regressed_at: "2026-11-03T07:50:00Z" }] },
      "GET /api/v1/findings/plan": { currency: "USD", buckets: [
        { key: "this_week", label: "This week", why: "low effort and low risk", count: 1, savings_monthly: 186.4,
          items: [{ id: "f1", title: "Lower requests", savings_monthly: 186.4, risk: "low", effort: "low", confidence: "high", team: null }] },
        { key: "this_month", label: "This month", why: "", count: 0, savings_monthly: 0, items: [] },
        { key: "later", label: "Later", why: "", count: 0, savings_monthly: 0, items: [] }] },
      "GET /api/v1/commitments": { accounts: [{ connection_id: "c1", name: "Payer", taken_at: "2026-11-08T05:50:00Z",
        savings_plans: { utilization_pct: 96.4, coverage_pct: 58, unused_commitment: 126, net_savings: 1418, total_commitment: 3504 },
        reservations: { utilization_pct: 100, coverage_pct: 0, net_savings: 0 } }] },
    });
    renderRoute("/savings");
    expect(await screen.findByText("1 overlapping finding(s) counted once.")).toBeInTheDocument();
    const commitments = await screen.findByRole("region", { name: "Commitments for Payer" });
    expect(commitments).toHaveTextContent("58%");
    expect(commitments).toHaveTextContent("96%");
    expect(screen.getAllByText("$186").length).toBeGreaterThan(0);
    expect(await screen.findByRole("region", { name: "This week" })).toHaveTextContent("Lower requests");
    expect(await screen.findByLabelText("Savings that came back")).toHaveTextContent("Downsize i-back");
    expect(screen.getByLabelText("Kept per month")).toHaveTextContent("$1,860");
    await userEvent.click(screen.getByRole("button", { name: /Lower requests for payments-gateway/ }));
    expect(screen.getByText(/kubectl -n payments set resources/)).toBeInTheDocument();
    expect(await screen.findByText(/Snapshot taken/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();  // members cannot approve
    await userEvent.selectOptions(screen.getByLabelText("Owner"), "u2");
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ owner_id: "u2" }));
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    const form = screen.getByRole("form", { name: "Dismiss" });
    expect(within(form).queryByRole("option", { name: "This check everywhere" })).not.toBeInTheDocument();  // admins only
    await userEvent.selectOptions(within(form).getByLabelText("For"), "resource");
    await userEvent.type(within(form).getByLabelText("Note"), "payments team owns this");
    await userEvent.click(within(form).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/v1/findings/f1/dismiss")?.body).toEqual({
      reason: "not_applicable", scope: "resource", note: "payments team owns this" }));
  });
});

describe("AWS advice on findings", () => {
  it("shows which AWS tools agree and marks imported advice", async () => {
    const agreed = { ...FINDING, evidence: { also_recommended_by: [
      { source: "compute_optimizer", label: "AWS Compute Optimizer", title: "EC2 instance overprovisioned", savings_monthly: 150 }] } };
    const imported = { ...FINDING, id: "f2", detector: "aws.imported_advice", category: "imported",
      title: "Rightsize Lambda checkout", evidence: { imported: true, sources: [
        { source: "cost_optimization_hub", label: "AWS Cost Optimization Hub", title: "Rightsize Lambda checkout", savings_monthly: 40 }] } };
    mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "member" }),
      "GET /api/v1/findings": { summary: { count: 2, savings_monthly: 226.4, overlapping: 0, by_category: {} },
        items: [agreed, imported], detectors: [] },
      "GET /api/v1/org/members": [],
      "GET /api/v1/findings/f1/history": [],
      "GET /api/v1/commitments": { accounts: [] },
    });
    renderRoute("/savings");
    expect(await screen.findByText("AWS agrees")).toBeInTheDocument();
    expect(screen.getByText("from AWS")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Lower requests for payments-gateway/ }));
    const list = screen.getByRole("list", { name: "AWS recommendations" });
    expect(list).toHaveTextContent("Also recommended by: AWS Compute Optimizer");
    expect(list).toHaveTextContent("$150");
  });
});

describe("tickets and pull requests", () => {
  it("opens a Jira ticket from a finding and shows the link", async () => {
    let links: unknown[] = [];
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "member" }),
      "GET /api/v1/findings": { summary: { count: 1, savings_monthly: 186.4, overlapping: 0, by_category: {} }, items: [FINDING], detectors: [] },
      "GET /api/v1/org/members": [],
      "GET /api/v1/findings/f1/history": [],
      "GET /api/v1/commitments": { accounts: [] },
      "GET /api/v1/org/work-integrations": [
        { id: "w1", kind: "jira", name: "Ops Jira", base_url: "https://acme.atlassian.net", username: "bot@acme.com", token_hint: "…oken",
          config: { project_key: "OPS" }, enabled: true, last_used_at: null, last_error: null },
        { id: "w2", kind: "github", name: "infra", base_url: "https://api.github.com", username: null, token_hint: "…abcd",
          config: { repo: "acme/infra" }, enabled: false, last_used_at: null, last_error: null }],
      "GET /api/v1/findings/f1/links": () => links,
      "POST /api/v1/findings/f1/links": () => {
        links = [{ id: "l1", kind: "ticket", provider: "jira", external_id: "OPS-42", url: "https://acme.atlassian.net/browse/OPS-42", created_at: "" }];
        return links[0];
      },
    });
    renderRoute("/savings");
    await userEvent.click(await screen.findByRole("button", { name: /Lower requests for payments-gateway/ }));
    const select = await screen.findByLabelText("Open in");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Ops Jira (Jira ticket)"]); // turned-off ones hidden
    await userEvent.click(screen.getByRole("button", { name: "Open ticket / PR" }));
    expect(await screen.findByRole("link", { name: /Ticket OPS-42/ })).toHaveAttribute("href", "https://acme.atlassian.net/browse/OPS-42");
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/findings/f1/links")?.body).toEqual({ integration_id: "w1" });
  });
});

describe("ordered advice", () => {
  it("explains replaced and ordered suggestions", async () => {
    const replaced = { ...FINDING, id: "f3", title: "Move i-1 from m5.large to m6i.large", confidence: "low",
      evidence: { pipeline: { superseded_by: "After downsizing, move i-1 to Graviton m7g.large", stack: false } } };
    const ordered = { ...FINDING, id: "f4", title: "After downsizing, move i-1 to Graviton m7g.large",
      evidence: { pipeline: { step: "generation", stack: true, order: ["Downsize i-1", "After downsizing, move i-1 to Graviton m7g.large"] },
        confidence: { level: "medium", reasons: ["priced from your bill", "the software must run on Arm"] } } };
    mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "member" }),
      "GET /api/v1/findings": { summary: { count: 2, savings_monthly: 90, overlapping: 0, by_category: {} },
        items: [ordered, replaced], detectors: [] },
      "GET /api/v1/org/members": [], "GET /api/v1/findings/f3/history": [], "GET /api/v1/findings/f4/history": [],
      "GET /api/v1/commitments": { accounts: [] },
    });
    renderRoute("/savings");
    expect(await screen.findByText("replaced by a better option")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^After downsizing/ }));
    expect(screen.getByText(/Do these in order/)).toHaveTextContent("Downsize i-1 → After downsizing");
    expect(screen.getByText("low confidence")).toBeInTheDocument();
    expect(screen.getByText(/Medium confidence:/).parentElement).toHaveTextContent("priced from your bill; the software must run on Arm.");
  });
});

describe("AWS audit", () => {
  const SUMMARY = {
    checks: 3, counts: { saving: 1, more_data: 1, manual: 0, fine: 1 },
    by_area: {}, savings_monthly: 120, uncovered_spend_monthly: 0, changed: 1,
    areas: { compute: "Compute", storage: "Storage", other: "Other services" },
  };
  const RUN = { id: "r1", started_at: "2026-11-02T07:30:00Z", finished_at: "2026-11-02T07:30:05Z", status: "done",
    trigger: "scheduled", requested_by: null, currency: "USD", summary: SUMMARY };
  const RESULTS = [
    { check_id: "ec2.rightsizing", area: "compute", service: "Amazon EC2", title: "Idle and over-sized instances",
      status: "more_data", reason: "No CloudWatch usage collected yet.", spend_monthly: 300, savings_monthly: 0,
      finding_count: 0, details: {} },
    { check_id: "ebs.unattached", area: "storage", service: "Amazon EBS", title: "Unattached volumes",
      status: "saving", reason: "2 suggestion(s) worth 120.00 USD a month.", spend_monthly: 300, savings_monthly: 120,
      finding_count: 2, details: { previous_status: "fine" } },
    { check_id: "ebs.snapshots", area: "storage", service: "Amazon EBS", title: "Old snapshots of deleted volumes",
      status: "fine", reason: "Checked 4 resource(s); nothing to change.", spend_monthly: 300, savings_monthly: 0,
      finding_count: 0, details: {} },
  ];

  it("shows each check's result, filters it and runs a new audit", async () => {
    let runs = [RUN];
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/audits": () => runs,
      "GET /api/v1/audits/r1": { run: RUN, results: RESULTS },
      "GET /api/v1/audits/r2": { run: { ...RUN, id: "r2", trigger: "manual", requested_by: "Asha" }, results: RESULTS },
      "POST /api/v1/audits": () => { runs = [{ ...RUN, id: "r2" }, RUN]; return runs[0]; },
    });
    renderRoute("/savings/audit");
    expect(await screen.findByRole("heading", { name: "Storage" })).toBeInTheDocument();
    expect(screen.getByText("Unattached volumes")).toBeInTheDocument();
    expect(screen.getByText("was: Checked & fine")).toBeInTheDocument();
    expect(screen.getByText(/1 check\(s\) changed/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Result"), "more_data");
    expect(screen.queryByText("Unattached volumes")).not.toBeInTheDocument();
    expect(screen.getByText("Idle and over-sized instances")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Run audit/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/audits")).toBe(true));
    expect(await screen.findByText(/run by Asha/)).toBeInTheDocument();
  });

  it("explains an empty history", async () => {
    mockApi({ "GET /api/v1/auth/me": makeMe(), "GET /api/v1/audits": [] });
    renderRoute("/savings/audit");
    expect(await screen.findByText("No audit yet")).toBeInTheDocument();
  });
});

describe("commitments inventory", () => {
  it("lists plans by expiry, shows contract progress and adds a contract", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let purchases: any[] = [];
    const item = { id: "c1", kind: "savings_plan", commitment_id: "arn:sp", account_id: "111", region: null,
      description: "Compute", count: 1, hourly_commitment: 4.8, upfront: null, recurring_hourly: null, currency: "USD",
      term_years: 1, payment_option: "No Upfront", offering_class: null, starts_at: null, ends_at: "2026-12-02T00:00:00Z",
      state: "active", utilization_pct: 72, days_left: 24 };
    const contract = { id: "k1", name: "AWS EDP", provider: "aws", kind: "edp", commitment_amount: 1000000, currency: "USD",
      discount_pct: 6, starts_on: "2026-01-01", ends_on: "2027-12-31", notes: "",
      progress: { spent: 300000, progress_pct: 30, expected_pct: 43.5, forecast: 690000, shortfall: 310000, days_left: 418 } };
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/commitments/inventory": { kinds: { savings_plan: "Savings Plan" }, items: [item], contracts: [contract] },
      "POST /api/v1/commitments/contracts": contract,
      "GET /api/v1/commitments/policy": { max_monthly_commitment: null, currency: "USD", max_term: "1yr",
        allowed_payments: ["no_upfront"], max_coverage_pct: 80, preview_days: 7 },
      "GET /api/v1/commitments/purchases": () => purchases,
      "POST /api/v1/commitments/purchases": (init: RequestInit | undefined) => {
        const body = JSON.parse(String(init?.body));
        purchases = [{ ...body, id: "p1", status: "planned", currency: "USD", commitment_id: null, purchased_on: null, verification: [] }];
        return purchases[0];
      },
      "POST /api/v1/commitments/purchases/p1/approve": () => {
        purchases = [{ ...purchases[0], status: "approved" }];
        return purchases[0];
      },
      "GET /api/v1/commitments/plan": { currency: "USD", settings: {}, reservations: [
        { service: "Amazon Relational Database Service", region: "ap-south-1", instance_type: "db.r6g.large", count: 3,
          running_peak: 4, on_demand_hourly: 0.25, discount: 0.31, monthly_saving: 169.7,
          ladder: [{ when: "now", count: 2 }, { when: "in 3 months", count: 1 }] }],
        savings_plan: { status: "ok", percentile: 10, steady_on_demand_hourly: 20, pending_changes_monthly: 730,
          after_changes_on_demand_hourly: 19, discount: 0.27, discount_source: "typical published rate",
          total_hourly_commitment: 13.87, monthly_saving: 3744.9, window: { start: "", end: "", days: 60 },
          ladder: [{ date: "2026-11-08", hourly_commitment: 3.47, monthly_saving: 936, reason: "first rung" }] } },
    });
    renderRoute("/savings/commitments");
    expect(await screen.findByText("Savings Plan: Compute")).toBeInTheDocument();
    expect(screen.getByText("24 days left")).toBeInTheDocument();
    expect(screen.getByText("72%")).toBeInTheDocument();
    expect(screen.getByText(/on course to fall short by \$310,000/)).toBeInTheDocument();
    expect(await screen.findByText(/Compute Savings Plan: \$13.87\/hour in total/)).toBeInTheDocument();
    expect(screen.getByText(/3 × db.r6g.large/)).toHaveTextContent("2 now, 1 in 3 months");
    await userEvent.click(screen.getByRole("button", { name: "Plan this" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/v1/commitments/purchases" && c.method === "POST")?.body)
      .toMatchObject({ kind: "savings_plan", hourly_commitment: 3.47, monthly_cost: 2533.1, term: "1yr" }));
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByText("approved")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Buy automatically" })).toBeInTheDocument(); // approved Savings Plans only
    expect(screen.getByRole("form", { name: "Purchase rules" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Add contract/ }));
    await userEvent.type(screen.getByLabelText("Name"), "GCP commit");
    await userEvent.type(screen.getByLabelText("Committed spend"), "50000");
    await userEvent.type(screen.getByLabelText("Starts"), "2026-01-01");
    await userEvent.type(screen.getByLabelText("Ends"), "2026-12-31");
    await userEvent.click(screen.getByRole("button", { name: "Save contract" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/v1/commitments/contracts")?.body).toMatchObject({
      name: "GCP commit", commitment_amount: 50000, starts_on: "2026-01-01", ends_on: "2026-12-31" }));
  });
});

describe("ROI pack", () => {
  it("shows verified savings against the price, with downloads", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/roi": { organisation: "Acme", currency: "USD", period: { start: "2026-09-01", end: "2026-10-31", months: 2 },
        verified_total: 1200, estimated_monthly: 50, product_cost: 200, product_currency: "USD", roi_multiple: 6, net: 1000,
        verified: [{ kind: "finding", title: "Downsize i-1", resource: "i-1", confirmed: "2026-08-20", before_daily: 10,
          after_daily: 3.4, months: 2, monthly: 200, total: 380, status: "verified" }],
        estimated: [{ title: "Downsize i-3", status: "applied", monthly: 50 }], notes: ["Verified savings are measured on your bill."] },
      "GET /api/v1/roi/settings": { monthly_price: 100, currency: "USD", since: null },
    });
    renderRoute("/savings/roi");
    expect(await screen.findByText("6×")).toBeInTheDocument();
    expect(screen.getByText("Downsize i-1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /PDF/ })).toHaveAttribute("href", "/api/v1/roi/pack.pdf?");
    expect(screen.getByRole("form", { name: "CloudCostRadar price" })).toBeInTheDocument();
  });
});

describe("upgrades", () => {
  it("shows the cost of staying and what is coming up", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/findings/upgrades": { staying_monthly: 3071.2,
        items: [{ id: "u1", title: "Upgrade Aurora cluster legacy out of Extended Support", detector: "aws.aurora_extended_support",
          staying_monthly: 583.7, currency: "USD", basis: "list price", standard_end: "2024-10-31",
          upgrade_to: "Aurora MySQL 3 (MySQL 8.0 compatible)", effort: "high" }],
        coming_up: [{ resource: "shared", kind: "EKS", version: "1.32", ends: "2027-03-23", upgrade_to: "the next Kubernetes minor version", estimated_monthly_usd: 364.8 }] },
    });
    renderRoute("/savings/upgrades");
    expect(await screen.findByText(/Cost of staying: \$3,071 a month/)).toBeInTheDocument();
    expect(screen.getByText("Aurora MySQL 3 (MySQL 8.0 compatible)")).toBeInTheDocument();
    expect(screen.getByLabelText("Coming up")).toHaveTextContent("EKS shared");
  });
});

describe("automatic fixes", () => {
  it("asks for a fix on a fixable finding and lets another admin approve", async () => {
    const vol = { ...FINDING, id: "f7", detector: "aws.ebs_unattached", category: "waste", title: "Delete unattached volume vol-1", resource_id: "vol-1", provider: "aws" };
    let rows: unknown[] = [];
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "admin" }),
      "GET /api/v1/findings": { summary: { count: 1, savings_monthly: 8, overlapping: 0, by_category: {} }, items: [vol], detectors: [] },
      "GET /api/v1/org/members": [], "GET /api/v1/findings/f7/history": [], "GET /api/v1/commitments": { accounts: [] },
      "GET /api/v1/fixes": () => rows,
      "POST /api/v1/findings/f7/fix": () => {
        rows = [{ id: "x1", finding_id: "f7", finding: vol.title, action: "delete_volume", label: "Snapshot, then delete the volume", resource_id: "vol-1",
          region: "eu-west-1", savings_monthly: 8, status: "requested", requested_by: "someone@else.com", approved_by: null, executed_at: null, result: {}, created_at: "" }];
        return rows[0];
      },
      "POST /api/v1/fixes/x1/approve": { id: "x1" },
    });
    renderRoute("/savings");
    await userEvent.click(await screen.findByRole("button", { name: /Delete unattached volume vol-1/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Ask CloudCostRadar to fix this" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/findings/f7/fix")).toBe(true));
  });
});
