import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

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
    ...extra,
  };
}

describe("settings", () => {
  it("connects a Kubernetes cluster and shows the install command once", async () => {
    const cluster = { id: "k1", name: "prod-eu", status: "waiting", agent_version: null, kubernetes_version: null,
      provider: null, node_count: null, last_seen_at: null, created_at: "", token_hint: "abc123", health: "waiting", objects: {} };
    const { calls } = mockApi(base({
      "POST /api/v1/clusters": { ...cluster, token: "ckt_k1_secret", install_command: "helm upgrade --install costlens-agent x --set costlens.token=ckt_k1_secret" },
    }));
    renderRoute("/settings?tab=kubernetes");
    expect(await screen.findByText("No clusters yet")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Cluster name"), "prod-eu");
    await userEvent.click(screen.getByRole("button", { name: /Connect cluster/ }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/v1/clusters" && c.method === "POST")?.body).toEqual({ name: "prod-eu" }));
    expect(await screen.findByText(/helm upgrade --install costlens-agent/)).toBeInTheDocument();
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
        }],
        next_cursor: null,
      },
    }));
    renderRoute("/settings?tab=audit");
    expect(await screen.findByText("role: member → admin")).toBeInTheDocument();
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

describe("cloud accounts", () => {
  it("connects AWS in two steps", async () => {
    const pending = { id: "c1", provider: "aws", name: "Production", status: "pending", external_id: "costlens-abc",
      role_arn: null, account_id: null, cur_bucket: null, cur_prefix: null, last_checked_at: null, last_error: null };
    const { calls } = mockApi(base({
      "POST /api/v1/connections/aws": pending,
      "GET /api/v1/connections/c1/aws-setup": { external_id: "costlens-abc", principal_arn: "arn:p", role_name: "CostLensReadOnly",
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
    expect(await within(dialog).findByText("costlens-abc")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Role ARN"), "arn:aws:iam::123456789012:role/CostLensReadOnly");
    await userEvent.click(within(dialog).getByRole("button", { name: "Verify and connect" }));
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/v1/connections/c1/verify")?.body).toEqual({
        role_arn: "arn:aws:iam::123456789012:role/CostLensReadOnly",
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
