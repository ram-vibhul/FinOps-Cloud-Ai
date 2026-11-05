/** Minimal typed client for the CostLens API. Cookies carry the session. */

/** RFC 9457 problem details, as returned by the API for every error. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  code: string;
  detail?: string;
  request_id?: string;
  errors?: { loc: (string | number)[]; msg: string; type: string }[];
}

function isProblem(body: unknown): body is Problem {
  return !!body && typeof body === "object" && "status" in body && "title" in body;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | undefined;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.code = isProblem(body) ? body.code : "http_error";
    this.requestId = isProblem(body) ? body.request_id : undefined;
  }
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

let sessionCsrf: string | undefined;

/** Called with the token from /me, which is always computed for the current session. */
export function setCsrfToken(token: string | undefined): void {
  sessionCsrf = token;
}

/** The CSRF token: from /me when known, else the readable cookie set at sign-in. */
export function csrfToken(): string | undefined {
  if (sessionCsrf) return sessionCsrf;
  const match = document.cookie.split("; ").find((c) => c.startsWith("costlens_csrf="));
  return match ? decodeURIComponent(match.slice("costlens_csrf=".length)) : undefined;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const method = (init.method ?? "GET").toUpperCase();
  const token = csrfToken();
  if (!SAFE_METHODS.has(method) && token) headers.set("X-CSRF-Token", token);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    const message = isProblem(body) ? (body.detail ?? body.title) : response.statusText || "Request failed";
    throw new ApiError(response.status, message, body);
  }
  return body as T;
}

export interface InstanceMeta {
  product: string;
  vendor: string;
  version: string;
  deployment_mode: "local" | "saas" | "self_hosted";
  environment: string;
  ai_enabled: boolean;
}

export type Role = "viewer" | "member" | "admin" | "owner";

export interface Organization {
  id: string;
  name: string;
  slug: string;
  role: Role;
  is_demo: boolean;
}

export interface Me {
  user: { id: string; email: string; name: string; avatar_url: string | null };
  organizations: Organization[];
  active_organization_id: string | null;
  role: Role | null;
  csrf_token: string;
}

export interface Provider {
  id: string;
  name: string;
}

export interface OrgDetails {
  id: string;
  name: string;
  slug: string;
  is_demo: boolean;
  default_currency: string;
  created_at: string;
}

export interface Member {
  user_id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  role: Role;
  joined_at: string;
}

export interface Invitation {
  id: string;
  email: string;
  role: Role;
  status: string;
  expires_at: string;
  invited_by: string | null;
}

export interface CreatedInvitation extends Invitation {
  invite_url: string;
}

export interface InvitationPreview {
  organization: string;
  role: Role;
  email_hint: string;
  invited_by: string | null;
  status: string;
}

export interface SsoProvider {
  id: string;
  provider_id: string;
  kind: "entra" | "okta" | "oidc";
  display_name: string;
  issuer: string;
  tenant: string | null;
  client_id: string;
  has_client_secret: boolean;
  trust_email: boolean;
  enabled: boolean;
  login_url: string;
}

export interface SsoProviderInput {
  kind: "entra" | "okta" | "oidc";
  display_name: string;
  client_id: string;
  client_secret: string;
  issuer?: string;
  tenant?: string;
  domain?: string;
  trust_email?: boolean;
}

export interface AuditEvent {
  id: string;
  occurred_at: string;
  actor_type: string;
  actor_label: string;
  action: string;
  target_type: string;
  target_id: string | null;
  summary: string;
  changes: Record<string, [unknown, unknown]>;
  ip: string | null;
  request_id: string | null;
}

export interface DataExport {
  id: string;
  status: "pending" | "ready" | "failed";
  size_bytes: number | null;
  created_at: string;
  expires_at: string | null;
  download_url: string | null;
}

export interface RetentionSetting {
  kind: string;
  label: string;
  description: string;
  days: number;
  default_days: number;
  min_days: number;
  max_days: number;
}

export interface CloudConnection {
  id: string;
  provider: string;
  name: string;
  status: "pending" | "active" | "error";
  external_id: string;
  role_arn: string | null;
  account_id: string | null;
  cur_bucket: string | null;
  cur_prefix: string | null;
  last_checked_at: string | null;
  last_error: string | null;
}

export interface AwsSetup {
  external_id: string;
  principal_arn: string;
  role_name: string;
  cloudformation: string;
  terraform: string;
  cli_command: string;
}

export interface FileImport {
  id: string;
  filename: string;
  size_bytes: number;
  file_type: "csv" | "parquet";
  detected_format: "focus" | "custom";
  status: "uploaded" | "queued" | "importing" | "imported" | "failed";
  columns: string[];
  mapping: Record<string, unknown>;
  report: ImportReport | Record<string, never>;
  rows_imported: number;
  created_at: string;
}

export interface ImportReport {
  rows_read: number;
  rows_valid: number;
  error_count: number;
  errors: { row: number; message: string }[];
  totals: Record<string, string>;
  first_date: string | null;
  last_date: string | null;
  billing_periods: Record<string, number>;
  refused?: boolean;
}

export interface ImportSettings {
  mapping?: Record<string, string>;
  provider?: string;
  currency?: string;
  date_format?: string;
  allow_partial?: boolean;
}

export type CostMetric = "effective" | "billed";
export type CostDimension = "service" | "account" | "region" | "charge_category" | "pricing_category" | "none";

export interface CostSummary {
  currency: string;
  metric: CostMetric;
  data_through: string | null;
  month_to_date: number;
  previous_month_to_date: number;
  change_vs_previous_month_pct: number | null;
  last_month: number;
  latest_day: number;
  forecast: {
    amount: number;
    low: number;
    high: number;
    daily_rate: number;
    recurring_expected: number;
    change_vs_last_month_pct: number | null;
    method: string;
  };
  other_currencies: Record<string, number>;
}

export interface CostSeries {
  currency: string;
  periods: string[];
  series: { key: string; label: string; total: number; values: number[] }[];
  total: number;
}

export interface CostMovers {
  currency: string;
  current_period: [string, string];
  previous_period: [string, string];
  items: { key: string; current: number; previous: number; change: number; change_pct: number | null; status: "new" | "gone" | "changed" }[];
}

export interface CostQuery {
  currency?: string;
  metric?: CostMetric;
  dimension?: CostDimension;
  start?: string;
  end?: string;
  months?: number;
  days?: number;
  top?: number;
}

function costQuery(q: CostQuery): string {
  return query(Object.fromEntries(Object.entries(q).map(([k, v]) => [k, v === undefined ? undefined : String(v)])));
}

export interface CostAnomaly {
  id: string;
  day: string;
  last_day: string;
  days: number;
  peak_impact: number;
  label: string;
  dimension: "total" | "service" | "account";
  key: string;
  currency: string;
  actual: number;
  expected: number;
  upper: number;
  impact: number;
  score: number;
  status: "open" | "acknowledged" | "expected" | "resolved";
  note: string | null;
}

export interface AlertSettings {
  anomaly_alerts: boolean;
  sensitivity: "low" | "medium" | "high";
  min_impact: number;
  ai_explanations?: boolean;
}

export interface ExploreQuery {
  start?: string;
  end?: string;
  days?: number;
  metric?: CostMetric;
  currency?: string;
  group_by: string[];
  filters: Record<string, string[]>;
  granularity: "total" | "day" | "month";
  top?: number;
  limit?: number;
}

export interface ExploreGroup {
  key: string[];
  label: string;
  total: number;
  share: number;
  previous: number;
  change_pct: number | null;
  resource?: { name: string | null; type: string | null; service: string | null; region: string | null; account: string | null } | null;
}

export interface ExploreResult extends CostSeries {
  group_by: string[];
  source: "daily" | "line_items";
  group_count: number;
  groups: ExploreGroup[];
}

export interface SavedView {
  id: string;
  name: string;
  shared: boolean;
  query: ExploreQuery;
  mine: boolean;
}

export interface ChangeEvent {
  id: string;
  occurred_at: string;
  source: string;
  kind: "deployment" | "infrastructure" | "config" | "incident" | "other";
  title: string;
  description: string | null;
  service: string | null;
  account: string | null;
  environment: string | null;
  url: string | null;
  actor: string | null;
  effect?: { scope: string; before_per_day: number; after_per_day: number; change_per_day: number; change_pct: number | null; days_after: number } | null;
}

export interface Timeline {
  periods: string[];
  values: number[];
  changes: ChangeEvent[];
}

export interface InboundWebhook {
  id: string;
  kind: string;
  name: string;
  url: string;
  last_received_at: string | null;
  secret?: string | null;
}

export interface DataQuality {
  currency: string;
  taggable_cost: number;
  required_tags: string[];
  coverage: { key: string; untagged_cost: number; coverage_pct: number; top_untagged_services: { service: string; cost: number }[] }[];
  unallocated: { tag: string; cost: number; pct: number };
  top_unallocated_resources: { resource_id: string; service: string; cost: number }[];
  inconsistent_keys: { canonical: string; suggested: string; variants: { key: string; cost: number }[] }[];
  inconsistent_values: { key: string; canonical: string; suggested: string; variants: { value: string; cost: number }[] }[];
}

export interface TagRule {
  kind: "key" | "value";
  key?: string | null;
  from: string[];
  to: string;
}

export interface PermissionReport {
  checked_at: string;
  role: "ok" | "error";
  message: string;
  results: { permission: string; feature: string; status: "ok" | "missing" | "unavailable" | "error" | "skipped"; message: string }[];
  missing: string[];
  policy_snippet: string | null;
}

export interface CloudResource {
  id: string;
  provider: string;
  account_id: string | null;
  region: string | null;
  service: string;
  resource_type: string;
  resource_id: string;
  name: string | null;
  state: string | null;
  owner: string | null;
  age_days: number | null;
  cost_30d: number | null;
  cost_source: string | null;
  tags: Record<string, string>;
  attributes: Record<string, unknown>;
}

export interface ResourceList {
  currency: string;
  total: number;
  by_type: Record<string, number>;
  items: CloudResource[];
}

export interface HourlyCosts {
  currency: string;
  hours: string[];
  series: { key: string; label: string; total: number; values: number[] }[];
}

export interface VTagMatch {
  account?: string[];
  service?: string[];
  region?: string[];
  tag?: Record<string, string[]>;
  resource_contains?: string[];
  usage_type_contains?: string[];
}

export interface VirtualTagDef {
  key: string;
  description?: string;
  default_value?: string | null;
  override_existing?: boolean;
  rules: { value: string; match: VTagMatch }[];
}

export interface VirtualTag extends VirtualTagDef {
  id: string;
}

export interface VTagPreview {
  values: { value: string; cost: number }[];
  lines_by_source: Record<string, number>;
  lines: number;
}

export interface AnomalyExplanation {
  text: string;
  template_text?: string;
  worded_by: "template" | "ai";
  drivers: { by: string; value: string; per_day: number; usual_per_day: number; increase_per_day: number; new: boolean }[];
  changes: { id: string; title: string; source: string; occurred_at: string; url: string | null }[];
}

export interface FxOverrideIn {
  from_currency: string;
  to_currency: string;
  start_day: string;
  end_day: string;
  rate: string;
  note?: string;
}

export interface FxOverride extends FxOverrideIn {
  id: string;
}

export interface K8sCluster {
  id: string;
  name: string;
  status: string;
  agent_version: string | null;
  kubernetes_version: string | null;
  provider: string | null;
  node_count: number | null;
  last_seen_at: string | null;
  created_at: string;
  token_hint: string;
  health: "waiting" | "ok" | "silent" | "outdated";
  objects: Record<string, number>;
}

export interface K8sClusterWithToken extends K8sCluster {
  token: string;
  install_command: string;
}

export interface AuditPage {
  items: AuditEvent[];
  next_cursor: string | null;
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

function query(params: Record<string, string | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  const text = q.toString();
  return text ? `?${text}` : "";
}

export const api = {
  meta: () => apiFetch<InstanceMeta>("/api/v1/meta"),
  me: () => apiFetch<Me>("/api/v1/auth/me"),
  providers: () => apiFetch<Provider[]>("/api/v1/auth/providers"),
  orgProviders: (slug: string) =>
    apiFetch<Provider[]>(`/api/v1/auth/organizations/${encodeURIComponent(slug)}/providers`),
  localLogin: (email: string, name: string) =>
    apiFetch<null>("/api/v1/auth/local", { method: "POST", ...json({ email, name }) }),
  logout: () => apiFetch<null>("/api/v1/auth/logout", { method: "POST" }),
  switchOrg: (organizationId: string) =>
    apiFetch<null>("/api/v1/auth/switch", { method: "POST", ...json({ organization_id: organizationId }) }),
  createOrg: (name: string) =>
    apiFetch<OrgDetails>("/api/v1/organizations", { method: "POST", ...json({ name }) }),
  joinDemo: () => apiFetch<{ organization_id: string }>("/api/v1/demo/join", { method: "POST" }),
  org: () => apiFetch<OrgDetails>("/api/v1/org"),
  updateOrg: (patch: Partial<Pick<OrgDetails, "name" | "default_currency">>) =>
    apiFetch<OrgDetails>("/api/v1/org", { method: "PATCH", ...json(patch) }),
  members: () => apiFetch<Member[]>("/api/v1/org/members"),
  changeRole: (userId: string, role: Role) =>
    apiFetch<Member>(`/api/v1/org/members/${userId}`, { method: "PATCH", ...json({ role }) }),
  removeMember: (userId: string) => apiFetch<null>(`/api/v1/org/members/${userId}`, { method: "DELETE" }),
  transferOwnership: (userId: string) =>
    apiFetch<null>("/api/v1/org/transfer-ownership", { method: "POST", ...json({ user_id: userId }) }),
  invitations: () => apiFetch<Invitation[]>("/api/v1/org/invitations"),
  invite: (email: string, role: Role) =>
    apiFetch<CreatedInvitation>("/api/v1/org/invitations", { method: "POST", ...json({ email, role }) }),
  revokeInvitation: (id: string) => apiFetch<null>(`/api/v1/org/invitations/${id}`, { method: "DELETE" }),
  previewInvitation: (token: string) =>
    apiFetch<InvitationPreview>(`/api/v1/invitations/${encodeURIComponent(token)}`),
  acceptInvitation: (token: string) =>
    apiFetch<null>(`/api/v1/invitations/${encodeURIComponent(token)}/accept`, { method: "POST" }),
  ssoProviders: () => apiFetch<SsoProvider[]>("/api/v1/org/sso"),
  createSso: (input: SsoProviderInput) =>
    apiFetch<SsoProvider>("/api/v1/org/sso", { method: "POST", ...json(input) }),
  updateSso: (id: string, patch: Partial<Pick<SsoProvider, "enabled" | "display_name" | "trust_email">>) =>
    apiFetch<SsoProvider>(`/api/v1/org/sso/${id}`, { method: "PATCH", ...json(patch) }),
  deleteSso: (id: string) => apiFetch<null>(`/api/v1/org/sso/${id}`, { method: "DELETE" }),
  testSso: (id: string) =>
    apiFetch<{ ok: boolean; error: string | null }>(`/api/v1/org/sso/${id}/test`, { method: "POST" }),
  deleteMe: (confirmEmail: string) =>
    apiFetch<null>("/api/v1/me", { method: "DELETE", ...json({ confirm_email: confirmEmail }) }),
  connections: () => apiFetch<CloudConnection[]>("/api/v1/connections"),
  createAws: (input: { name: string; cur_bucket?: string; cur_prefix?: string }) =>
    apiFetch<CloudConnection>("/api/v1/connections/aws", { method: "POST", ...json(input) }),
  awsSetup: (id: string) => apiFetch<AwsSetup>(`/api/v1/connections/${id}/aws-setup`),
  verifyConnection: (id: string, roleArn: string) =>
    apiFetch<CloudConnection>(`/api/v1/connections/${id}/verify`, { method: "POST", ...json({ role_arn: roleArn }) }),
  collectNow: (id: string, months = 2) =>
    apiFetch<{ status: string }>(`/api/v1/connections/${id}/import`, { method: "POST", ...json({ months }) }),
  checkPermissions: (id: string) =>
    apiFetch<PermissionReport>(`/api/v1/connections/${id}/permissions-check`, { method: "POST" }),
  deleteConnection: (id: string) => apiFetch<null>(`/api/v1/connections/${id}`, { method: "DELETE" }),
  fileImports: () => apiFetch<FileImport[]>("/api/v1/imports/files"),
  uploadFile: (file: File) =>
    apiFetch<FileImport>(`/api/v1/imports/files?filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      body: file,
      headers: { "Content-Type": "application/octet-stream" },
    }),
  validateFile: (id: string, settings: ImportSettings) =>
    apiFetch<ImportReport>(`/api/v1/imports/files/${id}/validate`, { method: "POST", ...json(settings) }),
  importFile: (id: string, settings: ImportSettings) =>
    apiFetch<FileImport>(`/api/v1/imports/files/${id}/import`, { method: "POST", ...json(settings) }),
  deleteFileImport: (id: string) => apiFetch<null>(`/api/v1/imports/files/${id}`, { method: "DELETE" }),
  costSummary: (q: CostQuery = {}) => apiFetch<CostSummary>(`/api/v1/costs/summary${costQuery(q)}`),
  costDaily: (q: CostQuery = {}) => apiFetch<CostSeries>(`/api/v1/costs/daily${costQuery(q)}`),
  costMonthly: (q: CostQuery = {}) => apiFetch<CostSeries>(`/api/v1/costs/monthly${costQuery(q)}`),
  costMovers: (q: CostQuery = {}) => apiFetch<CostMovers>(`/api/v1/costs/movers${costQuery(q)}`),
  anomalies: (status = "open") => apiFetch<CostAnomaly[]>(`/api/v1/costs/anomalies?status=${status}`),
  updateAnomaly: (id: string, status: CostAnomaly["status"], note?: string) =>
    apiFetch<CostAnomaly>(`/api/v1/costs/anomalies/${id}`, { method: "PATCH", ...json({ status, note }) }),
  explainAnomaly: (id: string) => apiFetch<AnomalyExplanation>(`/api/v1/costs/anomalies/${id}/explanation`),
  fxOverrides: () => apiFetch<FxOverride[]>("/api/v1/fx/overrides"),
  createFxOverride: (body: FxOverrideIn) => apiFetch<FxOverride>("/api/v1/fx/overrides", { method: "POST", ...json(body) }),
  deleteFxOverride: (id: string) => apiFetch<null>(`/api/v1/fx/overrides/${id}`, { method: "DELETE" }),
  clusters: () => apiFetch<K8sCluster[]>("/api/v1/clusters"),
  createCluster: (name: string) => apiFetch<K8sClusterWithToken>("/api/v1/clusters", { method: "POST", ...json({ name }) }),
  rotateClusterToken: (id: string) => apiFetch<K8sClusterWithToken>(`/api/v1/clusters/${id}/rotate-token`, { method: "POST" }),
  deleteCluster: (id: string) => apiFetch<null>(`/api/v1/clusters/${id}`, { method: "DELETE" }),
  alertSettings: () => apiFetch<AlertSettings>("/api/v1/org/alert-settings"),
  saveAlertSettings: (settings: AlertSettings) =>
    apiFetch<AlertSettings>("/api/v1/org/alert-settings", { method: "PUT", ...json(settings) }),
  explore: (q: ExploreQuery) => apiFetch<ExploreResult>("/api/v1/costs/explore", { method: "POST", ...json(q) }),
  dimensionValues: (dimension: string, q?: string) =>
    apiFetch<{ value: string; total: number }[]>(
      `/api/v1/costs/dimensions/${encodeURIComponent(dimension)}/values${q ? `?q=${encodeURIComponent(q)}` : ""}`,
    ),
  tagKeys: () => apiFetch<{ key: string; tagged_cost: number; coverage_pct: number }[]>("/api/v1/costs/tags?days=90"),
  dimensionSettings: () => apiFetch<{ team_tag: string; project_tag: string }>("/api/v1/org/dimension-settings"),
  views: () => apiFetch<SavedView[]>("/api/v1/views"),
  saveView: (name: string, shared: boolean, view: ExploreQuery) =>
    apiFetch<SavedView>("/api/v1/views", { method: "POST", ...json({ name, shared, query: view }) }),
  deleteView: (id: string) => apiFetch<null>(`/api/v1/views/${id}`, { method: "DELETE" }),
  timeline: (days = 90) => apiFetch<Timeline>(`/api/v1/costs/timeline?days=${days}`),
  anomalyChanges: (id: string) => apiFetch<ChangeEvent[]>(`/api/v1/costs/anomalies/${id}/changes`),
  createChange: (input: { title: string; kind: ChangeEvent["kind"]; occurred_at?: string; service?: string; description?: string }) =>
    apiFetch<ChangeEvent>("/api/v1/changes", { method: "POST", ...json(input) }),
  webhooks: () => apiFetch<InboundWebhook[]>("/api/v1/org/webhooks"),
  createGithubWebhook: (name: string) =>
    apiFetch<InboundWebhook>("/api/v1/org/webhooks/github", { method: "POST", ...json({ name }) }),
  deleteWebhook: (id: string) => apiFetch<null>(`/api/v1/org/webhooks/${id}`, { method: "DELETE" }),
  dataQuality: (days = 30) => apiFetch<DataQuality>(`/api/v1/costs/data-quality?days=${days}`),
  qualitySettings: () => apiFetch<{ required_tags: string[] }>("/api/v1/org/data-quality-settings"),
  saveQualitySettings: (required_tags: string[]) =>
    apiFetch<{ required_tags: string[] }>("/api/v1/org/data-quality-settings", { method: "PUT", ...json({ required_tags }) }),
  tagRules: () => apiFetch<{ rules: TagRule[] }>("/api/v1/org/tag-rules"),
  saveTagRules: (rules: TagRule[]) => apiFetch<{ rules: TagRule[] }>("/api/v1/org/tag-rules", { method: "PUT", ...json({ rules }) }),
  applyTagRules: () => apiFetch<{ status: string }>("/api/v1/org/tag-rules/apply", { method: "POST" }),
  resources: (params: { resource_type?: string; state?: string; q?: string; sort?: string }) =>
    apiFetch<ResourceList>(`/api/v1/resources${query(params)}`),
  resource: (id: string) =>
    apiFetch<{ resource: CloudResource; currency: string; daily: { day: string; cost: number }[] }>(`/api/v1/resources/${id}`),
  hourly: (hours = 72) => apiFetch<HourlyCosts>(`/api/v1/costs/hourly?hours=${hours}`),
  virtualTags: () => apiFetch<VirtualTag[]>("/api/v1/virtual-tags"),
  saveVirtualTag: (def: VirtualTagDef, id?: string) =>
    apiFetch<VirtualTag>(id ? `/api/v1/virtual-tags/${id}` : "/api/v1/virtual-tags", { method: id ? "PUT" : "POST", ...json(def) }),
  deleteVirtualTag: (id: string) => apiFetch<null>(`/api/v1/virtual-tags/${id}`, { method: "DELETE" }),
  previewVirtualTag: (def: VirtualTagDef) =>
    apiFetch<VTagPreview>("/api/v1/virtual-tags/preview", { method: "POST", ...json({ definition: def }) }),
  retention: () => apiFetch<RetentionSetting[]>("/api/v1/org/retention"),
  updateRetention: (changes: Record<string, number>) =>
    apiFetch<RetentionSetting[]>("/api/v1/org/retention", { method: "PATCH", ...json(changes) }),
  exports: () => apiFetch<DataExport[]>("/api/v1/org/exports"),
  requestExport: () => apiFetch<DataExport>("/api/v1/org/exports", { method: "POST" }),
  deleteOrg: (confirmSlug: string) =>
    apiFetch<{ purge_after: string }>("/api/v1/org", { method: "DELETE", ...json({ confirm_slug: confirmSlug }) }),
  auditEvents: (params: { action?: string; actor?: string; cursor?: string }) =>
    apiFetch<AuditPage>(`/api/v1/org/audit-events${query(params)}`),
  auditExportUrl: (params: { action?: string; actor?: string }) =>
    `/api/v1/org/audit-events/export${query(params)}`,
};

export function loginUrl(providerId: string, returnTo?: string): string {
  const q = returnTo && returnTo !== "/" ? `?return_to=${encodeURIComponent(returnTo)}` : "";
  return `/api/v1/auth/login/${encodeURIComponent(providerId)}${q}`;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}
