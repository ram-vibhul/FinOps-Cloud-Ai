import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute, Reply } from "@/test/render";

const ORG = { id: "o1", name: "Acme", slug: "acme", is_demo: false, default_currency: "USD", created_at: "" };
const MEMBERS = [
  { user_id: "u1", email: "asha@acme.com", name: "Asha", avatar_url: null, role: "owner", joined_at: "" },
  { user_id: "u2", email: "ravi@acme.com", name: "Ravi", avatar_url: null, role: "member", joined_at: "" },
];

const RETENTION = {
  kind: "audit_log", label: "Audit log", description: "Who changed what.", days: 365,
  default_days: 365, min_days: 90, max_days: 2555,
};

function base(extra: Record<string, unknown> = {}) {
  return {
    "GET /api/v1/auth/me": makeMe(),
    "GET /api/v1/org": ORG,
    "GET /api/v1/org/members": MEMBERS,
    "GET /api/v1/org/invitations": [],
    "GET /api/v1/org/sso": [],
    "GET /api/v1/org/audit-events": { items: [], next_cursor: null },
    "GET /api/v1/org/exports": [],
    "GET /api/v1/org/retention": [RETENTION],
    "GET /api/v1/connections": [],
    "GET /api/v1/imports/files": [],
    "GET /api/v1/org/webhooks": [],
    "GET /api/v1/org/alert-settings": { anomaly_alerts: true, sensitivity: "medium", min_impact: 50 },
    "GET /api/v1/fx/overrides": [],
    "GET /api/v1/clusters": [],
    "GET /api/v1/org/notification-channels": [],
    "GET /api/v1/org/outgoing-webhooks": [],
    "GET /api/v1/org/teams": [],
    "GET /api/v1/org/scim": { enabled: false, token_hint: null, default_role: "member", base_url: "https://ccradar.example/scim/v2" },
    "GET /api/v1/org/domains": [],
    "GET /api/v1/org/elevated-access": [],
    "GET /api/v1/org/custom-roles": { grantable: ["read", "write", "manage_members", "manage_connections", "manage_settings"], roles: [] },
    "GET /api/v1/reliability-targets": [],
    ...extra,
  };
}

describe("settings", () => {
  it("adds a reliability target for a Kubernetes workload", async () => {
    const cluster = { id: "k1", name: "prod", status: "connected", agent_version: "0.1.0", kubernetes_version: null,
      provider: "aws", node_count: 3, last_seen_at: null, created_at: "", token_hint: "x", health: "ok", objects: {}, prices: null };
    const saved = { id: "t1", name: "Checkout", tier: "critical", availability_pct: 99.95, headroom_pct: 40,
      max_utilization_pct: 80, min_replicas: 3, notes: "",
      match: { kind: "k8s_workload", cluster_id: "k1", namespace: "shop", workload: "checkout" } };
    let list: unknown[] = [];
    const { calls } = mockApi(base({
      "GET /api/v1/clusters": [cluster],
      "GET /api/v1/reliability-targets": () => list,
      "POST /api/v1/reliability-targets": () => { list = [saved]; return saved; },
    }));
    renderRoute("/settings?tab=reliability");
    expect(await screen.findByText("No reliability targets yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Add target/ }));
    await userEvent.type(screen.getByLabelText("Name"), "Checkout");
    await userEvent.selectOptions(screen.getByLabelText("Applies to"), "k8s_workload");
    await userEvent.selectOptions(screen.getByLabelText("Tier"), "critical");
    await userEvent.selectOptions(await screen.findByLabelText("Cluster"), "k1");
    await userEvent.type(screen.getByLabelText("Namespace"), "shop");
    await userEvent.type(screen.getByLabelText("Workload"), "checkout");
    const head = screen.getByLabelText("Headroom above peak %");
    await userEvent.clear(head);
    await userEvent.type(head, "40");
    const reps = screen.getByLabelText("Minimum replicas");
    await userEvent.clear(reps);
    await userEvent.type(reps, "3");
    await userEvent.click(screen.getByRole("button", { name: "Save target" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
      name: "Checkout", tier: "critical", headroom_pct: 40, min_replicas: 3,
      match: { kind: "k8s_workload", cluster_id: "k1", namespace: "shop", workload: "checkout" } }));
    expect(await screen.findByText(/prod\/shop\/checkout/)).toBeInTheDocument();
  });

  it("sets custom prices for a cluster outside AWS", async () => {
    const cluster = { id: "k1", name: "dc1", status: "connected", agent_version: "0.1.0", kubernetes_version: null,
      provider: "other", node_count: 3, last_seen_at: null, created_at: "", token_hint: "x", health: "ok", objects: {}, prices: null };
    const { calls } = mockApi(base({ "GET /api/v1/clusters": [cluster], "PUT /api/v1/clusters/k1/prices": cluster }));
    renderRoute("/settings?tab=kubernetes");
    await userEvent.click(await screen.findByRole("button", { name: "Prices" }));
    await userEvent.selectOptions(screen.getByLabelText("Currency"), "INR");
    const cpu = screen.getByLabelText("Per vCPU-hour");
    await userEvent.clear(cpu);
    await userEvent.type(cpu, "2.5");
    const spot = screen.getByLabelText("Spot discount %");
    await userEvent.clear(spot);
    await userEvent.type(spot, "70");
    await userEvent.click(screen.getByRole("button", { name: "Save prices" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      currency: "INR", cpu_hour: 2.5, gib_hour: 0.004237, node_hour: 0, spot_discount_pct: 70, always: false }));
  });

  it("tests and adds a custom metric query", async () => {
    const cluster = { id: "k1", name: "prod", status: "connected", agent_version: "0.1.0", kubernetes_version: null,
      provider: null, node_count: 1, last_seen_at: null, created_at: "", token_hint: "x", health: "ok", objects: {},
      agent: { usage_source: "prometheus", prometheus_url: "http://prometheus-operated.monitoring.svc:9090", prometheus_discovered: true } };
    const { calls } = mockApi(base({
      "GET /api/v1/clusters": [cluster],
      "GET /api/v1/k8s/clusters/k1/metric-queries": [],
      "POST /api/v1/k8s/clusters/k1/tasks": { id: "t1", kind: "promql", params: {}, status: "done", result: "30  queue=mail", error: null, created_at: "", finished_at: "" },
      "GET /api/v1/k8s/tasks/t1": { id: "t1", kind: "promql", params: {}, status: "done", result: "30  queue=mail", error: null, created_at: "", finished_at: "" },
      "POST /api/v1/k8s/clusters/k1/metric-queries": { id: "q1" },
    }));
    renderRoute("/settings?tab=kubernetes");
    await userEvent.click(await screen.findByRole("button", { name: "Metrics" }));
    expect(await screen.findByText("found automatically")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Query name"), "Queue depth");
    await userEvent.type(screen.getByLabelText("PromQL"), "sum by (queue) (queue_depth)");
    await userEvent.click(screen.getByRole("button", { name: /Test/ }));
    expect(await screen.findByText("30 queue=mail", {}, { timeout: 5000 })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add query" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path.endsWith("/metric-queries"))?.body).toEqual({
      name: "Queue depth", promql: "sum by (queue) (queue_depth)", unit: "" }));
  });

  it("chooses watched namespaces for a cluster", async () => {
    const cluster = { id: "k1", name: "prod", status: "connected", agent_version: "0.1.0", kubernetes_version: null,
      provider: null, node_count: 1, last_seen_at: null, created_at: "", token_hint: "x", health: "ok", objects: {},
      namespaces: ["billing", "kube-system", "shop"], include_namespaces: [], exclude_namespaces: ["kube-system"] };
    const { calls } = mockApi(base({ "GET /api/v1/clusters": [cluster], "PATCH /api/v1/clusters/k1": cluster }));
    renderRoute("/settings?tab=kubernetes");
    await userEvent.click(await screen.findByRole("button", { name: "Namespaces" }));
    await userEvent.click(screen.getByLabelText("Only the ticked ones"));
    await userEvent.click(screen.getByLabelText("kube-system"));
    await userEvent.click(screen.getByLabelText("shop"));
    await userEvent.click(screen.getByRole("button", { name: "Save namespaces" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      include_namespaces: ["shop"], exclude_namespaces: [] }));
  });

  it("adds an Alertmanager receiver and shows the setup once", async () => {
    const cluster = { id: "k1", name: "prod", status: "connected", agent_version: "0.1.0", kubernetes_version: null,
      provider: null, node_count: 1, last_seen_at: null, created_at: "", token_hint: "x", health: "ok", objects: {} };
    const { calls } = mockApi(base({
      "GET /api/v1/clusters": [cluster],
      "POST /api/v1/org/webhooks/alerts": { id: "h1", kind: "alertmanager", name: "Prometheus Alertmanager",
        url: "https://c.example/api/v1/hooks/alertmanager/h1", last_received_at: null, secret: "s3",
        setup: "receivers:\n  - name: ccradar\n    webhook_configs:\n      - url: https://c.example/api/v1/hooks/alertmanager/h1" },
    }));
    renderRoute("/settings?tab=kubernetes");
    await userEvent.click(await screen.findByRole("button", { name: /Add receiver/ }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/v1/org/webhooks/alerts")?.body).toEqual({
      kind: "alertmanager", name: "Prometheus Alertmanager", cluster_id: "k1" }));
    expect(await screen.findByText(/webhook_configs/)).toBeInTheDocument();
  });

  it("adds a Slack channel and sends a test", async () => {
    const channel = { id: "c1", kind: "slack", name: "#oncall", url_hint: "…XXXXXX", events: ["k8s_incident"], enabled: true,
      last_sent_at: null, last_error: null };
    let channels: unknown[] = [];
    const { calls } = mockApi(base({
      "GET /api/v1/org/notification-channels": () => channels,
      "POST /api/v1/org/notification-channels": () => { channels = [channel]; return channel; },
      "POST /api/v1/org/notification-channels/c1/test": channel,
    }));
    renderRoute("/settings?tab=notifications");
    expect(await screen.findByText("No channels yet")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Name"), "#oncall");
    await userEvent.type(screen.getByLabelText("Webhook address"), "https://hooks.slack.com/services/T/B/X");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText("#oncall")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/org/notification-channels")?.body).toEqual({
      kind: "slack", name: "#oncall", url: "https://hooks.slack.com/services/T/B/X" });
    await userEvent.click(screen.getByRole("button", { name: /Send test/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/org/notification-channels/c1/test")).toBe(true));
  });

  it("lists partner clients, shows re-billing and makes a link code", async () => {
    const me = makeMe({ role: "owner" });
    me.organizations = me.organizations.map((o) => ({ ...o, is_partner: true }));
    const { calls } = mockApi(base({
      "GET /api/v1/auth/me": me,
      "GET /api/v1/partner/clients": [{ id: "c1", name: "Acme Retail", currency: "USD", access: "owner", markup_pct: 10,
        month_to_date: 50, last_month: 133.33, last_month_rebilled: 146.66 }],
      "GET /api/v1/partner/clients/c1/rebill": { client: "Acme Retail", month: "2026-10", currency: "USD", markup_pct: 10,
        lines: [{ service: "Amazon EC2", cost: 100, rebilled: 110 }], cost: 133.33, rebilled: 146.66, margin: 13.33 },
      "POST /api/v1/partner/link-codes": { code: "clp_abc", valid_days: 7 },
      "GET /api/v1/partner/portfolio": [{ id: "c1", name: "Acme Retail", currency: "USD", attention: 50, reasons: ["1 connection(s) failing"],
        month_to_date: 50, last_month: 133.33, savings_open_monthly: 0 }],
    }));
    renderRoute("/settings?tab=partner");
    expect(await within(await screen.findByRole("table", { name: "Clients" })).findByText("Acme Retail")).toBeInTheDocument(); // rows arrive later
    await userEvent.click(screen.getByRole("button", { name: "Re-billing" }));
    expect(await screen.findByRole("region", { name: "Re-billing" })).toHaveTextContent("margin $13.33");
    expect(screen.getByRole("link", { name: /Download the client's statement/ })).toHaveAttribute("href", "/api/v1/partner/clients/c1/statement.pdf?month=2026-10");
    expect(screen.getByLabelText("Portfolio")).toHaveTextContent("1 connection(s) failing");
    await userEvent.click(screen.getByRole("button", { name: "Link an existing organisation" }));
    expect(await screen.findByLabelText("Partner code")).toHaveTextContent("clp_abc");
    expect(calls.some((c) => c.path === "/api/v1/partner/clients/c1/rebill")).toBe(true);
  });

  it("creates a custom role limited to an account", async () => {
    const { calls } = mockApi(base({
      "POST /api/v1/org/custom-roles": { id: "r1", name: "Web analyst", description: "", permissions: ["read", "write"], accounts: ["111"], teams: [], members: 0 },
    }));
    renderRoute("/settings?tab=members");
    await userEvent.type(await screen.findByLabelText("Role name"), "Web analyst");
    await userEvent.click(screen.getByLabelText("Act on savings, budgets and reports"));
    await userEvent.type(screen.getByLabelText("Only these accounts (optional)"), "111");
    await userEvent.click(screen.getByRole("button", { name: "Add role" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/org/custom-roles")?.body).toEqual({
      name: "Web analyst", permissions: ["read", "write"], accounts: ["111"], teams: [] }));
  });

  it("lets an owner apply to become a partner", async () => {
    const { calls } = mockApi(base({
      "GET /api/v1/auth/me": makeMe({ role: "owner" }),
      "POST /api/v1/org/partner-application": { status: "pending" },
    }));
    renderRoute("/settings?tab=general");
    await userEvent.type(await screen.findByLabelText("Company"), "Reseller Pvt Ltd");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByText(/We will review your application/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/v1/org/partner-application")?.body).toEqual({ company: "Reseller Pvt Ltd", website: "", message: "" });
  });

  it("asks for temporary admin access", async () => {
    let rows: unknown[] = [];
    const me = makeMe({ role: "member" });
    const { calls } = mockApi(base({
      "GET /api/v1/auth/me": me,
      "GET /api/v1/org/elevated-access": () => rows,
      "POST /api/v1/org/elevated-access": () => {
        rows = [{ id: "e1", user_id: me.user.id, user: me.user.email, role: "admin", minutes: 240, reason: "rotate the AWS role", status: "requested",
          decided_by: null, starts_at: null, expires_at: null, created_at: "" }];
        return rows[0];
      },
    }));
    renderRoute("/settings?tab=members");
    await userEvent.selectOptions(await screen.findByLabelText("For"), "240");
    await userEvent.type(screen.getByLabelText("Reason"), "rotate the AWS role");
    await userEvent.click(screen.getByRole("button", { name: "Ask for access" }));
    expect(await screen.findByLabelText("Access requests")).toHaveTextContent("requested");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/org/elevated-access")?.body).toEqual({ role: "admin", minutes: 240, reason: "rotate the AWS role" });
  });

  it("turns on SCIM and shows the token once", async () => {
    const { calls } = mockApi(base({
      "POST /api/v1/org/scim": { enabled: true, token_hint: "abc123", default_role: "viewer", base_url: "https://ccradar.example/scim/v2", token: "scim_secret_abc123" },
    }));
    renderRoute("/settings?tab=sso");
    expect(await screen.findByText("https://ccradar.example/scim/v2")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("New people join as"), "viewer");
    await userEvent.click(screen.getByRole("button", { name: "Turn on SCIM" }));
    expect(await screen.findByLabelText("SCIM token")).toHaveTextContent("scim_secret_abc123");
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/org/scim")?.body).toEqual({ default_role: "viewer" });
  });

  it("adds a domain and shows the TXT record to create", async () => {
    let domains: unknown[] = [];
    const d = { id: "d1", domain: "acme.com", verified_at: null, last_checked_at: null, last_error: null,
      record: { name: "_ccradar-verification.acme.com", type: "TXT", value: "ccradar-verification=abc" } };
    const { calls } = mockApi(base({
      "GET /api/v1/org/domains": () => domains,
      "POST /api/v1/org/domains": () => { domains = [d]; return d; },
      "POST /api/v1/org/domains/d1/verify": () => { domains = [{ ...d, verified_at: "2026-11-09T00:00:00Z" }]; return domains[0]; },
      "GET /api/v1/org/domain-policy": { enforce_sso: false, auto_join: false, auto_join_role: "viewer" },
    }));
    renderRoute("/settings?tab=members");
    await userEvent.type(await screen.findByLabelText("Domain"), "acme.com");
    await userEvent.click(screen.getByRole("button", { name: "Add domain" }));
    expect(await screen.findByText("_ccradar-verification.acme.com")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check now" }));
    expect(await screen.findByText("verified")).toBeInTheDocument();
    expect(await screen.findByLabelText("Auto-join role")).toHaveValue("viewer");
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/org/domains")?.body).toEqual({ domain: "acme.com" });
  });

  it("creates a read-only API key and shows it once", async () => {
    let keys: unknown[] = [];
    const created = { id: "k1", name: "finance dashboard", scope: "read", key_hint: "abc123", created_by: "o@acme.com",
      created_at: "2026-11-09T00:00:00Z", expires_at: null, last_used_at: null, key: "clk_secret_abc123" };
    const { calls } = mockApi(base({
      "GET /api/v1/org/api-keys": () => keys,
      "POST /api/v1/org/api-keys": () => { keys = [{ ...created, key: null }]; return created; },
    }));
    renderRoute("/settings?tab=api-keys");
    await userEvent.type(await screen.findByLabelText("Key name"), "finance dashboard");
    await userEvent.click(screen.getByRole("button", { name: "Create key" }));
    expect(await screen.findByLabelText("New API key")).toHaveTextContent("clk_secret_abc123");
    expect(await screen.findByText("o@acme.com")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "finance dashboard", scope: "read" });
    expect(screen.getByLabelText("MCP configuration")).toHaveTextContent('"url": "http://localhost:3000/mcp"');
  });

  it("adds a webhook and shows its secret once", async () => {
    const hook = { id: "h1", name: "SIEM", url_hint: "https://hooks.example.com/…", events: ["budget_alert"], enabled: true,
      last_sent_at: null, last_status: null, last_error: null, secret: null };
    let hooks: unknown[] = [];
    const { calls } = mockApi(base({
      "GET /api/v1/org/outgoing-webhooks": () => hooks,
      "POST /api/v1/org/outgoing-webhooks": () => { hooks = [hook]; return { ...hook, secret: "whsec_abc" }; },
      "POST /api/v1/org/outgoing-webhooks/h1/test": { id: "d1", event: "ping", status: "sent", attempts: 1, last_status: 200,
        last_error: null, created_at: "", delivered_at: "", next_attempt_at: null },
    }));
    renderRoute("/settings?tab=notifications");
    expect(await screen.findByText("No webhooks yet.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Webhook name"), "SIEM");
    await userEvent.type(screen.getByLabelText("Endpoint (https://)"), "https://hooks.example.com/ccradar");
    await userEvent.click(screen.getByRole("button", { name: "Add webhook" }));
    expect(await screen.findByLabelText("Signing secret")).toHaveTextContent("whsec_abc");
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/org/outgoing-webhooks")?.body).toEqual({
      name: "SIEM", url: "https://hooks.example.com/ccradar" });
    const row = screen.getByText("SIEM").closest("li") as HTMLElement;
    expect(within(row).getByLabelText("Budget alerts")).toBeChecked();
    await userEvent.click(within(row).getByRole("button", { name: "Send test" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/org/outgoing-webhooks/h1/test")).toBe(true));
  });

  it("adds a GitHub integration for pull requests", async () => {
    const gh = { id: "w1", kind: "github", name: "infra", base_url: "https://api.github.com", username: null, token_hint: "…_tok",
      config: { repo: "acme/infra", base_branch: "main" }, enabled: true, last_used_at: null, last_error: null };
    let items: unknown[] = [];
    const { calls } = mockApi(base({
      "GET /api/v1/org/work-integrations": () => items,
      "POST /api/v1/org/work-integrations": () => { items = [gh]; return gh; },
    }));
    renderRoute("/settings?tab=integrations");
    expect(await screen.findByText("No integrations yet.")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Service"), "github");
    expect(screen.queryByLabelText(/Email of the Jira user/)).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Name"), "infra");
    await userEvent.type(screen.getByLabelText("API token"), "ghp_tok");
    await userEvent.type(screen.getByLabelText("Repository"), "acme/infra");
    await userEvent.type(screen.getByLabelText("Base branch"), "main");
    await userEvent.click(screen.getByRole("button", { name: "Add integration" }));
    expect(await screen.findByText(/acme\/infra · main · token …_tok/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/org/work-integrations")?.body).toEqual({
      kind: "github", name: "infra", token: "ghp_tok", config: { repo: "acme/infra", base_branch: "main" } });
  });

  it("connects a Kubernetes cluster and shows the install command once", async () => {
    const cluster = { id: "k1", name: "prod-eu", status: "waiting", agent_version: null, kubernetes_version: null,
      provider: null, node_count: null, last_seen_at: null, created_at: "", token_hint: "abc123", health: "waiting", objects: {} };
    const { calls } = mockApi(base({
      "POST /api/v1/clusters": { ...cluster, token: "ckt_k1_secret", install_command: "helm upgrade --install cloudcostradar-agent x --set ccradar.token=ckt_k1_secret" },
    }));
    renderRoute("/settings?tab=kubernetes");
    expect(await screen.findByText("No clusters yet")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Cluster name"), "prod-eu");
    await userEvent.click(screen.getByRole("button", { name: /Connect cluster/ }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/v1/clusters" && c.method === "POST")?.body).toEqual({ name: "prod-eu" }));
    expect(await screen.findByText(/helm upgrade --install cloudcostradar-agent/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText(/ckt_k1_secret/)).not.toBeInTheDocument();
  });

  it("adds an exchange-rate override for a month", async () => {
    const { calls } = mockApi(base({ "POST /api/v1/fx/overrides": { id: "f1" } }));
    renderRoute("/settings");
    expect(await screen.findByText("Exchange-rate overrides")).toBeInTheDocument();
    const month = screen.getByLabelText("Month");
    await userEvent.clear(month);
    await userEvent.type(month, "2026-02");
    await userEvent.selectOptions(screen.getByLabelText("of"), "INR");
    await userEvent.type(screen.getByLabelText("equals"), "84.5");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/v1/fx/overrides" && c.method === "POST")?.body).toEqual({
      from_currency: "USD", to_currency: "INR", start_day: "2026-02-01", end_day: "2026-02-28", rate: "84.5", note: "",
    }));
  });

  it("saves organisation details", async () => {
    const { calls } = mockApi(base({ "PATCH /api/v1/org": { ...ORG, name: "Acme Inc" } }));
    renderRoute("/settings");
    const name = await screen.findByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Acme Inc");
    await userEvent.selectOptions(screen.getByLabelText("Display currency"), "INR");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "Acme Inc", default_currency: "INR" }),
    );
  });

  it("changes a member's role", async () => {
    const { calls } = mockApi(base({ "PATCH /api/v1/org/members/u2": { ...MEMBERS[1], role: "admin" } }));
    renderRoute("/settings?tab=members");
    await userEvent.selectOptions(await screen.findByLabelText("Role for ravi@acme.com"), "admin");
    await waitFor(() => expect(calls.find((c) => c.path === "/api/v1/org/members/u2")?.body).toEqual({ role: "admin" }));
  });

  it("invites someone and shows the link", async () => {
    mockApi(base({
      "POST /api/v1/org/invitations": {
        id: "i1", email: "new@x.com", role: "member", status: "pending", expires_at: "", invited_by: "Asha",
        invite_url: "http://localhost/invite/abc",
      },
    }));
    renderRoute("/settings?tab=members");
    await userEvent.click(await screen.findByRole("button", { name: /Invite people/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Email"), "new@x.com");
    await userEvent.click(within(dialog).getByRole("button", { name: "Send invitation" }));
    expect(await within(dialog).findByDisplayValue("http://localhost/invite/abc")).toBeInTheDocument();
  });

  it("hides admin tabs from members", async () => {
    mockApi(base({ "GET /api/v1/auth/me": makeMe({ role: "member" }) }));
    renderRoute("/settings");
    expect(await screen.findByRole("tab", { name: "Members" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Audit log" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Single sign-on" })).not.toBeInTheDocument();
  });

  it("adds an Entra sign-in provider", async () => {
    const { calls } = mockApi(base({ "POST /api/v1/org/sso": { id: "p1" } }));
    renderRoute("/settings?tab=sso");
    expect(await screen.findByText("No company sign-in yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Add provider/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Button label"), "Contoso");
    await userEvent.type(within(dialog).getByLabelText("Tenant ID or domain"), "contoso.com");
    await userEvent.type(within(dialog).getByLabelText("Client ID"), "cid");
    await userEvent.type(within(dialog).getByLabelText("Client secret"), "sec");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add provider" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/org/sso")?.body).toEqual({
        kind: "entra", display_name: "Contoso", client_id: "cid", client_secret: "sec", tenant: "contoso.com",
      }),
    );
  });

  it("shows and filters the audit log", async () => {
    const { calls } = mockApi(base({
      "GET /api/v1/org/audit-events": {
        items: [{
          id: "e1", occurred_at: "2026-11-05T10:00:00Z", actor_type: "user", actor_label: "asha@acme.com",
          action: "membership.updated", target_type: "membership", target_id: "m1",
          summary: "membership updated", changes: { role: ["member", "admin"] }, ip: null, request_id: null,
        }, {
          id: "e2", occurred_at: "2026-11-05T09:00:00Z", actor_type: "user", actor_label: "asha@acme.com",
          action: "budget.created", target_type: "budget", target_id: "b1", summary: "budget created",
          changes: {
            id: [null, "0d9f3c1e-1111-4222-8333-944455556666"], org_id: [null, "x"],
            name: [null, "Platform"], owner_id: [null, "7a1b2c3d-1111-4222-8333-944455556666"],
          },
          ip: null, request_id: null,
        }],
        next_cursor: null,
      },
    }));
    renderRoute("/settings?tab=audit");
    expect(await screen.findByText("role: member → admin")).toBeInTheDocument();
    // Created rows: no bookkeeping ids, short ids, no "— →" before the new value.
    expect(screen.getByText("name: Platform; owner id: 7a1b2c3d…")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Action"), "membership");
    await userEvent.click(screen.getByRole("button", { name: "Filter" }));
    await waitFor(() => expect(calls.filter((c) => c.path === "/api/v1/org/audit-events")).toHaveLength(2));
    expect(screen.getByRole("link", { name: /Export CSV/ })).toHaveAttribute(
      "href",
      "/api/v1/org/audit-events/export?action=membership",
    );
  });
});

describe("privacy", () => {
  it("deletes the account only after typing the email", async () => {
    const { calls } = mockApi(base({ "DELETE /api/v1/me": null, "POST /api/v1/auth/logout": null, "GET /api/v1/auth/providers": [] }));
    const { router } = renderRoute("/settings?tab=account");
    expect(await screen.findByRole("link", { name: /Download my data/ })).toHaveAttribute("href", "/api/v1/me/export");
    await userEvent.click(screen.getByRole("button", { name: /Delete my account/ }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Delete my account" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/Type your email/), "asha@acme.com");
    await userEvent.click(confirm);
    await waitFor(() => expect(router.state.location.pathname).toBe("/sign-in"));
    expect(calls.find((c) => c.method === "DELETE" && c.path === "/api/v1/me")?.body).toEqual({
      confirm_email: "asha@acme.com",
    });
  });

  it("lets owners export and delete the organisation", async () => {
    const ready = { id: "x1", status: "ready", size_bytes: 4096, created_at: "2026-11-05T10:00:00Z",
                    expires_at: "2026-11-12T10:00:00Z", download_url: "/api/v1/org/exports/x1/download" };
    const { calls } = mockApi(base({
      "GET /api/v1/org/exports": [ready],
      "POST /api/v1/org/exports": ready,
      "DELETE /api/v1/org": { purge_after: "2026-12-05T10:00:00Z" },
    }));
    renderRoute("/settings");
    expect(await screen.findByRole("link", { name: /Download \(4 KB\)/ })).toHaveAttribute(
      "href", "/api/v1/org/exports/x1/download");
    await userEvent.click(screen.getByRole("button", { name: /Delete organisation/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Type the organisation address/), "acme");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete organisation" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "DELETE" && c.path === "/api/v1/org")?.body).toEqual({ confirm_slug: "acme" }),
    );
  });

  it("hides the owner zone from admins", async () => {
    mockApi(base({ "GET /api/v1/auth/me": makeMe({ role: "admin" }) }));
    renderRoute("/settings");
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete organisation/ })).not.toBeInTheDocument();
  });
});

describe("retention", () => {
  it("saves the number of days", async () => {
    const { calls } = mockApi(base({ "PATCH /api/v1/org/retention": [{ ...RETENTION, days: 730 }] }));
    renderRoute("/settings");
    const input = await screen.findByLabelText("Audit log (days)");
    await userEvent.clear(input);
    await userEvent.type(input, "730");
    await userEvent.click(screen.getByRole("button", { name: "Save retention" }));
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/v1/org/retention" && c.method === "PATCH")?.body).toEqual({ audit_log: 730 }),
    );
  });
});

describe("plan", () => {
  it("shows usage against each limit and the included features", async () => {
    mockApi(base({
      "GET /api/v1/org/plan": {
        plan: { id: "free", name: "Free" }, source: "default", placeholder: true,
        features: [{ id: "kubernetes", label: "Kubernetes clusters", included: true }, { id: "sso", label: "Single sign-on", included: false }],
        limits: [
          { key: "members", label: "People", limit: 3, used: 2 },
          { key: "api_calls_daily", label: "API calls a day", limit: null, used: null },
        ],
      },
      "GET /api/v1/org/usage": {
        now: {},
        days: [{ day: "2026-11-08", members: 2, connections: 1, clusters: 0, cloud_spend_monthly_usd: 4321.5, api_calls: 1234, ai_summaries: 3 }],
      },
    }));
    renderRoute("/settings?tab=plan");
    expect(await screen.findByText("Free plan")).toBeInTheDocument();
    expect(await screen.findByText("Usage history")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "1,234" })).toBeInTheDocument();
    expect(screen.getByText(/provisional/)).toBeInTheDocument();
    expect(screen.getByLabelText("People used")).toHaveAttribute("value", "2");
    expect(screen.getByText("Unlimited")).toBeInTheDocument();
    const sso = screen.getAllByRole("listitem").find((li) => li.textContent === "Single sign-on");
    expect(within(sso as HTMLElement).getByLabelText("Not included")).toBeInTheDocument();
  });
});

describe("subscription", () => {
  const PLAN = {
    plan: { id: "business", name: "Business" }, source: "chosen", placeholder: true, features: [],
    limits: [{ key: "members", label: "People", limit: 100, used: 2 }],
  };
  const SUB = {
    status: "active", plan: { id: "business", name: "Business" }, payments_available: false,
    choices: [{ id: "free", name: "Free" }, { id: "team", name: "Team" }, { id: "business", name: "Business" }],
    interval: "monthly", provider: "manual", trial_ends_at: null, current_period_end: "2026-12-09T10:00:00Z",
    cancel_at_period_end: false, pending_plan: null,
  };

  it("schedules a downgrade and cancels", async () => {
    const { calls } = mockApi(base({
      "GET /api/v1/org/plan": PLAN,
      "GET /api/v1/org/subscription": SUB,
      "POST /api/v1/org/subscription/change": { status: "scheduled", subscription: { ...SUB, pending_plan: { id: "team", name: "Team" } } },
      "POST /api/v1/org/subscription/cancel": { status: "cancelling", subscription: { ...SUB, cancel_at_period_end: true } },
    }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderRoute("/settings?tab=plan");
    expect(await screen.findByText(/Business, billed monthly; renews on/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Choose a plan"), "team");
    await userEvent.click(screen.getByRole("button", { name: "Change plan" }));
    expect(await screen.findByText(/Business until .*, then Team\./)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/v1/org/subscription/change")?.body).toEqual({ plan: "team", interval: "monthly" });
    await userEvent.click(screen.getByRole("button", { name: "Cancel subscription" }));
    expect(await screen.findByRole("button", { name: "Keep my subscription" })).toBeInTheDocument();
  });

  it("explains that upgrades need us until payments exist", async () => {
    const { calls } = mockApi(base({
      "GET /api/v1/org/plan": PLAN,
      "GET /api/v1/org/subscription": { ...SUB, status: "trialing", trial_ends_at: "2026-11-23T10:00:00Z" },
      "POST /api/v1/org/subscription/change": new Reply(409, {
        title: "Conflict", status: 409, code: "payment_unavailable",
        detail: "Online payment is not available yet. Contact us to upgrade and we will set it up.",
      }),
    }));
    renderRoute("/settings?tab=plan");
    expect(await screen.findByText(/Free trial of Business until/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "End the trial now" })).toBeInTheDocument();
    expect(screen.getByText(/to upgrade, contact us/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Change plan" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/org/subscription/change")).toBe(true));
    expect(screen.getByText(/Free trial of Business until/)).toBeInTheDocument(); // unchanged
  });
});

describe("licence", () => {
  const PLAN = { plan: { id: "business", name: "Business" }, source: "chosen", placeholder: true, features: [], limits: [] };
  const LIC = {
    deployment_mode: "self_hosted", state: "grace", problem: null,
    warning: "Your CloudCostRadar licence has expired. Everything keeps working until 23 November 2026; after that this installation moves to the free tier. Nothing is deleted.",
    id: "lic-1", customer: "Acme Ltd", plan: "business", expires_at: "2026-11-09T00:00:00Z", days_left: 0,
    grace_ends_at: "2026-11-23T00:00:00Z", grace_days_left: 13, limits: {}, features: [],
  };

  it("warns in the grace period and lets platform admins install a key", async () => {
    const { calls } = mockApi(base({
      "GET /api/v1/auth/me": makeMe({ role: "owner", user: { id: "u1", email: "asha@acme.com", name: "Asha", avatar_url: null, is_platform_admin: true } }),
      "GET /api/v1/org/plan": PLAN,
      "GET /api/v1/system/licence": LIC,
      "PUT /api/v1/system/licence": { ...LIC, state: "valid", warning: null, days_left: 365, expires_at: "2027-11-09T00:00:00Z" },
    }));
    renderRoute("/settings?tab=plan");
    expect(await screen.findByText("Licence expired: in its grace period.")).toBeInTheDocument();
    expect(screen.getAllByText(/keeps working until 23 November 2026/).length).toBe(2); // banner and card
    expect(screen.getByText("Acme Ltd")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Install a licence key"), "CCRv1.payload.signature");
    await userEvent.click(screen.getByRole("button", { name: "Install licence" }));
    expect(await screen.findByText("Licence active.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ key: "CCRv1.payload.signature" });
  });

  it("shows nothing outside self-hosted mode", async () => {
    mockApi(base({
      "GET /api/v1/org/plan": PLAN,
      "GET /api/v1/system/licence": { deployment_mode: "saas", state: "none", problem: null, warning: null },
    }));
    renderRoute("/settings?tab=plan");
    expect(await screen.findByText("Business plan")).toBeInTheDocument();
    expect(screen.queryByText("Licence")).not.toBeInTheDocument();
  });
});

describe("cloud accounts", () => {
  it("checks that the billing export can be read", async () => {
    const result = { ok: true, code: "ready", message: "CloudCostRadar can read the export: 2 files covering 2026-10, 2026-11.",
      checked_at: "2026-11-09T10:00:00Z", files: 2 };
    let conn: Record<string, unknown> = { id: "a1", provider: "aws", name: "Prod", status: "active", external_id: "x", role_arn: "arn",
      account_id: "123456789012", cur_bucket: "acme-cur", cur_prefix: "exports", last_checked_at: null, last_error: null };
    const { calls } = mockApi(base({
      "GET /api/v1/connections": () => [conn],
      "POST /api/v1/connections/a1/export-check": () => { conn = { ...conn, export_check: result }; return result; },
    }));
    renderRoute("/settings?tab=clouds");
    await userEvent.click(await screen.findByRole("button", { name: "Check export" }));
    expect(await screen.findByText(/Billing export: CloudCostRadar can read the export/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/connections/a1/export-check")).toBe(true);
  });

  it("connects Azure with a service principal", async () => {
    const conn = { id: "az1", provider: "azure", name: "Azure prod", status: "active", external_id: "azure-x", role_arn: null,
      account_id: "33333333-3333-3333-3333-3333333333", cur_bucket: null, cur_prefix: null, last_checked_at: null, last_error: null,
      azure: { scope: "/subscriptions/33333333-3333-3333-3333-333333333333" } };
    let conns: unknown[] = [];
    const { calls } = mockApi(base({
      "GET /api/v1/connections": () => conns,
      "POST /api/v1/connections/azure": () => { conns = [conn]; return conn; },
    }));
    renderRoute("/settings?tab=clouds");
    await userEvent.click(await screen.findByRole("button", { name: /Connect Azure/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Name"), "Azure prod");
    await userEvent.type(within(dialog).getByLabelText("Directory (tenant) ID"), "11111111-1111-1111-1111-111111111111");
    await userEvent.type(within(dialog).getByLabelText("Application (client) ID"), "22222222-2222-2222-2222-222222222222");
    await userEvent.type(within(dialog).getByLabelText("Client secret"), "s3cret-value");
    await userEvent.type(within(dialog).getByLabelText("Scope"), "/subscriptions/33333333-3333-3333-3333-333333333333");
    await userEvent.click(within(dialog).getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("/subscriptions/33333333-3333-3333-3333-333333333333")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/connections/azure")?.body).toEqual({
      name: "Azure prod", tenant_id: "11111111-1111-1111-1111-111111111111", client_id: "22222222-2222-2222-2222-222222222222",
      client_secret: "s3cret-value", scope: "/subscriptions/33333333-3333-3333-3333-333333333333" });
    expect(screen.queryByRole("button", { name: /Check permissions/ })).not.toBeInTheDocument();
  });

  it("connects Google Cloud with a key file", async () => {
    const conn = { id: "g1", provider: "gcp", name: "GCP", status: "active", external_id: "gcp-x", role_arn: null, account_id: "acme",
      cur_bucket: null, cur_prefix: null, last_checked_at: null, last_error: null,
      gcp: { table: "acme-billing.billing.export_v1", client_email: "c@acme.iam.gserviceaccount.com" } };
    let conns: unknown[] = [];
    const { calls } = mockApi(base({
      "GET /api/v1/connections": () => conns,
      "POST /api/v1/connections/gcp": () => { conns = [conn]; return conn; },
    }));
    renderRoute("/settings?tab=clouds");
    await userEvent.click(await screen.findByRole("button", { name: /Connect Google Cloud/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Name"), "GCP");
    const file = new File(['{"type":"service_account"}'], "key.json", { type: "application/json" });
    await userEvent.upload(within(dialog).getByLabelText("Service account key (JSON file)"), file);
    await userEvent.type(within(dialog).getByLabelText("Billing export table"), "acme-billing.billing.export_v1");
    await waitFor(() => expect(within(dialog).getByText(/Key loaded/)).toBeInTheDocument());
    await userEvent.click(within(dialog).getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("acme-billing.billing.export_v1")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/connections/gcp")?.body).toEqual({
      name: "GCP", service_account_key: '{"type":"service_account"}', table: "acme-billing.billing.export_v1", detailed: true });
  });

  it("connects Datadog with secret fields kept apart", async () => {
    const conn = { id: "d1", provider: "datadog", name: "Datadog", status: "active", external_id: "x", role_arn: null, account_id: null,
      cur_bucket: null, cur_prefix: null, last_checked_at: null, last_error: null, saas: { site: "datadoghq.eu" } };
    let conns: unknown[] = [];
    const { calls } = mockApi(base({
      "GET /api/v1/connections": () => conns,
      "POST /api/v1/connections/saas": () => { conns = [conn]; return conn; },
    }));
    renderRoute("/settings?tab=clouds");
    await userEvent.click(await screen.findByRole("button", { name: /Connect a service/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.selectOptions(within(dialog).getByLabelText("Service"), "datadog");
    await userEvent.selectOptions(within(dialog).getByLabelText("Site"), "datadoghq.eu");
    await userEvent.type(within(dialog).getByLabelText("API key"), "ddapi");
    await userEvent.type(within(dialog).getByLabelText("Application key"), "ddapp");
    await userEvent.click(within(dialog).getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("datadoghq.eu")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/connections/saas")?.body).toEqual({
      provider: "datadog", name: "Datadog", settings: { site: "datadoghq.eu" }, secret: { api_key: "ddapi", app_key: "ddapp" } });
  });

  it("shows setup commands and the last collection for Google Cloud", async () => {
    const conn = { id: "g1", provider: "gcp", name: "GCP", status: "active", external_id: "x", role_arn: null, account_id: "acme",
      cur_bucket: null, cur_prefix: null, last_checked_at: null, last_error: "403: PERMISSION_DENIED", gcp: { table: "a.b.c" } };
    mockApi(base({
      "GET /api/v1/connections": [conn],
      "GET /api/v1/connections/g1/setup-help": {
        provider: "gcp", roles: [{ role: "roles/bigquery.dataViewer", scope: "dataset a:b", why: "Read the billing export table." }],
        commands: ["bq add-iam-policy-binding --member='serviceAccount:x' --role=roles/bigquery.dataViewer 'a:b'"],
        last_collection: { scope: "a.b.c", checked_at: null, error: "403: PERMISSION_DENIED", data_from: "2026-10-01", data_to: "2026-10-31", rows: 412,
          runs: [{ billing_period: "2026-10-01", status: "succeeded", rows: 412, total_cost: 99, currency: "USD", finished_at: "2026-11-01T00:00:00Z", error: null }],
          advice: { count: 3, errors: {} } },
      },
    }));
    renderRoute("/settings?tab=clouds");
    await userEvent.click(await screen.findByRole("button", { name: "Setup and last collection" }));
    const seen = await screen.findByRole("region", { name: "Last collection" });
    expect(seen).toHaveTextContent("412 cost row(s), 2026-10-01 to 2026-10-31");
    expect(seen).toHaveTextContent("Last error: 403: PERMISSION_DENIED");
    expect(screen.getByLabelText("Commands")).toHaveTextContent("bq add-iam-policy-binding");
    expect(screen.getByLabelText("Read-only roles")).toHaveTextContent("roles/bigquery.dataViewer");
  });

  it("connects AWS in two steps", async () => {
    const pending = { id: "c1", provider: "aws", name: "Production", status: "pending", external_id: "ccradar-abc",
      role_arn: null, account_id: null, cur_bucket: null, cur_prefix: null, last_checked_at: null, last_error: null };
    const { calls } = mockApi(base({
      "POST /api/v1/connections/aws": pending,
      "GET /api/v1/connections/c1/aws-setup": { external_id: "ccradar-abc", principal_arn: "arn:p", role_name: "CloudCostRadarReadOnly",
        cloudformation: "{}", terraform: "", cli_command: "aws cloudformation deploy ..." },
      "POST /api/v1/connections/c1/verify": { ...pending, status: "active", account_id: "123456789012" },
    }));
    renderRoute("/settings?tab=clouds");
    expect(await screen.findByText("No cloud accounts connected")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Connect AWS/ }));
    let dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Name"), "Production");
    await userEvent.click(within(dialog).getByRole("button", { name: "Continue" }));
    dialog = await screen.findByRole("dialog", { name: "Create the read-only role" });
    expect(await within(dialog).findByText("ccradar-abc")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Role ARN"), "arn:aws:iam::123456789012:role/CloudCostRadarReadOnly");
    await userEvent.click(within(dialog).getByRole("button", { name: "Verify and connect" }));
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/v1/connections/c1/verify")?.body).toEqual({
        role_arn: "arn:aws:iam::123456789012:role/CloudCostRadarReadOnly",
      }),
    );
  });
});

describe("collect now", () => {
  it("queues an import for an active connection", async () => {
    const active = { id: "c1", provider: "aws", name: "Production", status: "active", external_id: "x",
      role_arn: "arn", account_id: "123456789012", cur_bucket: null, cur_prefix: null,
      last_checked_at: "2026-11-05T10:00:00Z", last_error: null };
    const { calls } = mockApi(base({
      "GET /api/v1/connections": [active],
      "POST /api/v1/connections/c1/import": { status: "queued" },
    }));
    renderRoute("/settings?tab=clouds");
    await userEvent.click(await screen.findByRole("button", { name: "Collect now" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/v1/connections/c1/import")?.body).toEqual({ months: 2 }));
  });

  it("sets up Bedrock invocation logs with a role rule", async () => {
    const active = { id: "c1", provider: "aws", name: "Production", status: "active", external_id: "x",
      role_arn: "arn", account_id: "123456789012", cur_bucket: null, cur_prefix: null, last_checked_at: null, last_error: null };
    const { calls } = mockApi(base({
      "GET /api/v1/connections": [active],
      "PATCH /api/v1/connections/c1": { ...active, bedrock_logs: { bucket: "acme-bedrock-logs" } },
      "POST /api/v1/connections/c1/bedrock-logs/import": { files: 3, events: 120, unpriced: 0 },
    }));
    renderRoute("/settings?tab=clouds");
    await userEvent.click(await screen.findByRole("button", { name: "Bedrock logs" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Log bucket"), "acme-bedrock-logs");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add a rule" }));
    await userEvent.type(within(dialog).getByLabelText("Role contains 1"), "batch-worker");
    await userEvent.type(within(dialog).getByLabelText("App 1"), "reports");
    await userEvent.type(within(dialog).getByLabelText("Team 1"), "finance");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save and read now" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/connections/c1/bedrock-logs/import")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ bedrock_log_bucket: "acme-bedrock-logs", bedrock_log_prefix: "",
      bedrock_rules: [{ match: "batch-worker", app: "reports", team: "finance" }] });
  });
});

describe("billing files", () => {
  it("uploads, maps, checks and imports a custom CSV", async () => {
    const uploaded = { id: "f1", filename: "saas.csv", size_bytes: 10, file_type: "csv", detected_format: "custom",
      status: "uploaded", columns: ["Day", "Product", "Amount"], mapping: { service_name: "Product" }, report: {},
      rows_imported: 0, created_at: "" };
    const report = { rows_read: 3, rows_valid: 3, error_count: 0, errors: [], totals: { USD: "12.00" },
      first_date: "2026-11-01", last_date: "2026-11-03", billing_periods: { "2026-11": 3 } };
    const { calls } = mockApi(base({
      "POST /api/v1/imports/files": uploaded,
      "POST /api/v1/imports/files/f1/validate": report,
      "POST /api/v1/imports/files/f1/import": { ...uploaded, status: "queued" },
    }));
    renderRoute("/settings?tab=clouds");
    await userEvent.click(await screen.findByRole("button", { name: /Upload file/ }));
    const dialog = await screen.findByRole("dialog");
    const file = new File(["Day,Product,Amount\n"], "saas.csv", { type: "text/csv" });
    await userEvent.upload(within(dialog).getByLabelText("File"), file);
    await userEvent.selectOptions(await within(dialog).findByLabelText("Date *"), "Day");
    await userEvent.selectOptions(within(dialog).getByLabelText("Cost *"), "Amount");
    await userEvent.click(within(dialog).getByRole("button", { name: "Check file" }));
    expect(await within(dialog).findByText(/3 of 3 rows are valid/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Import" }));
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/v1/imports/files/f1/import")?.body).toEqual({
        mapping: { service_name: "Product", charge_date: "Day", cost: "Amount" },
        currency: "USD", provider: "other", allow_partial: false,
      }),
    );
  });
});

describe("alert settings", () => {
  it("saves sensitivity and minimum", async () => {
    const { calls } = mockApi(base({ "PUT /api/v1/org/alert-settings": { anomaly_alerts: true, sensitivity: "high", min_impact: 20 } }));
    renderRoute("/settings");
    await userEvent.selectOptions(await screen.findByLabelText("Sensitivity"), "high");
    const min = screen.getByLabelText(/Ignore increases/);
    await userEvent.clear(min);
    await userEvent.type(min, "20");
    await userEvent.click(screen.getByRole("button", { name: "Save alerts" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      anomaly_alerts: true, sensitivity: "high", min_impact: 20 }));
  });
});

describe("github webhooks", () => {
  it("creates a webhook and shows the secret once", async () => {
    mockApi(base({ "POST /api/v1/org/webhooks/github": { id: "h1", kind: "github", name: "GitHub",
      url: "https://app.example/api/v1/hooks/github/h1", last_received_at: null, secret: "s3cr3t" } }));
    renderRoute("/settings?tab=clouds");
    await userEvent.click(await screen.findByRole("button", { name: /Add GitHub webhook/ }));
    expect(await screen.findByDisplayValue("s3cr3t")).toBeInTheDocument();
    expect(screen.getByDisplayValue("https://app.example/api/v1/hooks/github/h1")).toBeInTheDocument();
  });
});

describe("permission check", () => {
  it("lists missing permissions with the policy fix", async () => {
    const active = { id: "c1", provider: "aws", name: "Production", status: "active", external_id: "x",
      role_arn: "arn", account_id: "123456789012", cur_bucket: null, cur_prefix: null, last_checked_at: null, last_error: null };
    mockApi(base({
      "GET /api/v1/connections": [active],
      "POST /api/v1/connections/c1/permissions-check": {
        checked_at: "", role: "ok", message: "", missing: ["ec2:DescribeVolumes"],
        policy_snippet: '{"Action": ["ec2:DescribeVolumes"]}',
        results: [
          { permission: "ec2:DescribeInstances", feature: "Resource inventory", status: "ok", message: "" },
          { permission: "ec2:DescribeVolumes", feature: "Unattached volumes", status: "missing", message: "" },
        ],
      },
    }));
    renderRoute("/settings?tab=clouds");
    await userEvent.click(await screen.findByRole("button", { name: "Check permissions" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Missing")).toBeInTheDocument();
    expect(within(dialog).getByText(/ec2:DescribeVolumes"\]/)).toBeInTheDocument();
  });
});

describe("ownership", () => {
  it("adds an owner rule for a tag", async () => {
    let rules: unknown[] = [];
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/org/members": [{ user_id: "u2", email: "ravi@acme.com", name: "Ravi", avatar_url: null, role: "member", joined_at: "" }],
      "GET /api/v1/findings/owner-rules": () => rules,
      "POST /api/v1/findings/owner-rules": (init: RequestInit | undefined) => {
        const body = JSON.parse(String(init?.body));
        rules = [{ ...body, id: "r1", findings: 3, savings_monthly: 410 }];
        return rules[0];
      },
    });
    renderRoute("/settings?tab=ownership");
    expect(await screen.findByText("No owner rules yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Add rule/ }));
    await userEvent.type(screen.getByLabelText("Name"), "Payments");
    await userEvent.type(screen.getByLabelText("Value"), "payments");
    await userEvent.type(screen.getByLabelText("Team"), "payments");
    await userEvent.selectOptions(screen.getByLabelText("Person"), "u2");
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "Payments", priority: 100, kind: "tag", key: "team", value: "payments", team: "payments", owner_id: "u2" }));
    expect(await screen.findByText(/3 finding\(s\)/)).toBeInTheDocument();
  });
});
