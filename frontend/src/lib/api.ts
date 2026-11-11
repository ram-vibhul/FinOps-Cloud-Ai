/** Minimal typed client for the CloudCostRadar API. Cookies carry the session. */

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
  const match = document.cookie.split("; ").find((c) => c.startsWith("ccradar_csrf="));
  return match ? decodeURIComponent(match.slice("ccradar_csrf=".length)) : undefined;
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
  is_partner?: boolean;
  partner_id?: string | null;
}

export interface PartnerClient {
  id: string; name: string; currency: string; access: string; markup_pct: number; month_to_date: number; last_month: number; last_month_rebilled: number;
}
export interface Rebill {
  client: string; month: string; currency: string; markup_pct: number; lines: { service: string; cost: number; rebilled: number }[];
  cost: number; rebilled: number; margin: number;
}
export interface PortfolioRow {
  id: string; name: string; currency: string; attention: number; reasons: string[]; month_to_date: number; last_month: number; savings_open_monthly: number;
}
export interface Branding { display_name: string; primary_color: string; logo?: string }

export interface Me {
  user: { id: string; email: string; name: string; avatar_url: string | null; is_platform_admin?: boolean };
  organizations: Organization[];
  active_organization_id: string | null;
  role: Role | null;
  csrf_token: string;
  permissions?: string[] | null;
  scope?: { accounts?: string[]; teams?: string[] } | null;
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
  azure?: Record<string, string> | null;
  gcp?: Record<string, string> | null;
  saas?: Record<string, string> | null;
  bedrock_logs?: Record<string, string> | null;
  export_check?: ExportCheck | null;
}

export interface ExportCheck {
  ok: boolean; code: string; message: string; checked_at: string;
  location?: string; files?: number; months?: string[];
}

export interface SetupHelp {
  provider: "azure" | "gcp";
  roles: { role: string; scope: string; why: string }[];
  commands: string[];
  last_collection?: {
    scope: string | null; checked_at: string | null; error: string | null; data_from: string | null; data_to: string | null; rows: number;
    runs: { billing_period: string; status: string; rows: number; total_cost: number | null; currency: string | null; finished_at: string | null; error: string | null }[];
    advice: { fetched_at?: string; count?: number; errors?: Record<string, string> } | null;
  };
}

export type SaasProvider = "snowflake" | "databricks" | "datadog" | "mongodb_atlas";

export interface GcpCreate {
  name: string; service_account_key: string; table: string; query_project?: string; detailed: boolean;
}

export interface AzureCreate {
  name: string; tenant_id: string; client_id: string; client_secret: string; scope: string;
  storage_account?: string; container?: string; prefix?: string;
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
export type CostDimension = "provider" | "service" | "account" | "region" | "charge_category" | "pricing_category" | "none";

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

export interface ProviderRow {
  provider: string; name: string; month_to_date: number; previous_month_to_date: number;
  change_pct: number | null; last_month: number; share_pct: number; data_through: string | null;
  connections: { id: string; name: string; status: string; error: string | null }[];
}
export interface ProvidersView {
  currency: string; metric: CostMetric; data_through: string; month_to_date: number;
  providers: ProviderRow[]; other_currencies: Record<string, number>;
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
  forecast_warnings?: boolean;
  forecast_over_pct?: number;
  same_day_warnings?: boolean;
  same_day_over_pct?: number;
  weekly_digest_email?: boolean;
}

export interface EarlyWarning {
  kind: "forecast" | "same_day"; over_pct: number; forecast?: number; last_month?: number; so_far?: number; usual?: number;
  hours?: number; drivers: { key: string; change: number }[];
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
  chart?: "bar" | "line" | "table"; // how the saved report is drawn (not sent to the API)
  top?: number;
  limit?: number;
}

export interface AskAnswer {
  query: Required<Pick<ExploreQuery, "start" | "end" | "metric" | "group_by" | "filters" | "granularity">>;
  chart: "bar" | "line" | "table";
  explanation: string;
}

export interface LeadershipReport {
  month: string; month_label: string; currency: string; closed: boolean; total: number; previous_total: number;
  change_pct: number | null; sentences: string[];
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
  health: "waiting" | "ok" | "silent" | "outdated" | "revoked";
  namespaces?: string[];
  include_namespaces?: string[];
  exclude_namespaces?: string[];
  agent?: { usage_source?: string; prometheus_url?: string | null; prometheus_discovered?: boolean };
  prices?: ClusterPrices | null;
  objects: Record<string, number>;
  first_scan?: FirstScan | null;
}

export interface K8sClusterWithToken extends K8sCluster {
  token: string;
  install_command: string;
}

export interface K8sCostItem {
  key: string;
  label: string;
  cost: number;
  share_pct: number | null;
  cpu: number;
  memory_gib: number;
  estimated: boolean;
  idle?: boolean;
  namespace?: string;
  kind?: string;
  cluster?: string | null;
}

export interface K8sCosts {
  currency: string;
  total: number;
  allocated: number;
  idle: number;
  idle_pct: number | null;
  estimated_pct: number | null;
  hours: number;
  daily: { day: string; allocated: number; idle: number }[];
  items: K8sCostItem[];
  clusters: { id: string; name: string }[];
}

export interface RightsizingItem {
  cluster: string | null;
  cluster_id: string;
  namespace: string;
  kind: string;
  workload: string;
  container: string;
  replicas: number;
  hours: number;
  cpu_request: number;
  memory_request_gib: number;
  memory_limit_gib: number | null;
  cpu_avg: number;
  cpu_peak: number;
  memory_avg_gib: number;
  memory_peak_gib: number;
  workload_monthly_cost: number;
  monthly_saving: number;
  action: "increase" | "decrease" | "set_requests" | "ok" | "learning";
  oom_risk: boolean;
  recommended_cpu: number;
  recommended_memory_gib: number;
  recommended_memory_limit_gib: number | null;
}

export interface Rightsizing {
  currency: string;
  headroom_pct: number;
  days: number;
  monthly_saving: number;
  items: RightsizingItem[];
}

export interface K8sWorkload {
  namespace: string;
  kind: string;
  name: string;
  pods: number;
  running: number;
  restarts: number;
  reasons: string[];
  health: "failing" | "warning" | "ok";
}

export interface InvestigationCause {
  check: string;
  title: string;
  confidence: number;
  summary: string;
  evidence: string[];
  fixes: string[];
  verify: string[];
}

export interface Correlation {
  currency: string;
  anchor: { at: string; title: string; source: string; url: string | null } | null;
  first_warning: string | null;
  effects: { signal: string; label: string; before: number; after: number; change_pct: number | null; text: string }[];
  story: string[];
  series: { hours: string[]; cost: (number | null)[]; cpu: (number | null)[]; memory: (number | null)[]; warnings: (number | null)[] };
  markers: { at: string; label: string; type: "change" | "incident" }[];
  cost_anomalies: { day: string; label: string; impact: number; currency: string }[];
}

export interface FixCheck {
  verdict: "fixed" | "improving" | "not_fixed" | "too_early";
  text: string;
  health_now: string;
  fix_at: string;
  incident_start: string;
  periods: { period: "before" | "during" | "after"; start: string; end: string; failures_per_hour: number; failures: number;
    cpu: number | null; memory_gib: number | null; cost_per_hour: number | null }[];
  side_effects: string[];
}

export interface InvestigationResult {
  target: string;
  summary: string;
  narrative?: string;
  status_update?: string;
  worded_by?: "template" | "ai";
  correlation?: Correlation;
  fix_check?: FixCheck;
  causes: InvestigationCause[];
  facts: {
    pods: number; running: number; restarts: number; warning_events: number; nodes: string[]; changes: string[]; logs: string;
    pod_list?: { name: string; namespace: string; containers: string[]; restarts: number }[];
  };
  timeline: { at: string; type: string; reason: string; object: string; message: string; count: number }[];
  window_start?: string;
  window_end?: string;
}

export interface InvestigationTarget {
  cluster_id: string;
  namespace?: string;
  kind?: string;
  name?: string;
  hours?: number;
  start?: string;
  end?: string;
}

export interface SavedInvestigation {
  id: string;
  cluster_id: string;
  cluster: string | null;
  namespace: string | null;
  kind: string | null;
  name: string | null;
  title: string;
  status: string;
  trigger: string;
  window_start: string;
  window_end: string;
  created_at: string;
  result?: InvestigationResult;
  real_cause?: string | null;
  cause_note?: string | null;
  fix_note?: string | null;
  top_cause?: string | null;
  cause_rank?: number | null;
  closed_at?: string | null;
}

export interface DiagnosisAccuracy {
  closed: number;
  top1_pct: number | null;
  top3_pct: number | null;
  missed: number;
  checks: { check: string; label: string; ranked_first: number; real: number; precision_pct: number | null; recall_pct: number | null }[];
  by_month: { month: string; closed: number; top1_pct: number | null }[];
  lab: { accuracy: number; correct: number; scenarios: number; kind: string } | null;
  causes: { check: string; label: string }[];
}

export interface WorkIntegration {
  id: string; kind: "jira" | "servicenow" | "github" | "gitlab"; name: string; base_url: string; username: string | null;
  token_hint: string; config: Record<string, string>; enabled: boolean; last_used_at: string | null; last_error: string | null;
}

export interface WorkIntegrationIn {
  kind: WorkIntegration["kind"]; name: string; base_url?: string; username?: string; token: string; config: Record<string, string>;
}

export interface FindingLink {
  id: string; kind: "ticket" | "pull_request"; provider: string; external_id: string; url: string; created_at: string;
}

export interface OutgoingWebhook {
  id: string; name: string; url_hint: string; events: string[]; enabled: boolean; last_sent_at: string | null;
  last_status: number | null; last_error: string | null; secret: string | null;
}

export interface WebhookDeliveryRow {
  id: string; event: string; status: "pending" | "sent" | "failed"; attempts: number; last_status: number | null;
  last_error: string | null; created_at: string; delivered_at: string | null; next_attempt_at: string | null;
}

export interface AiRow { key: string; cost_usd: number; requests: number; input_tokens: number; output_tokens: number }
export interface AiSummary {
  start: string; end: string;
  cloud_services: { provider: string; service: string; currency: string; cost: number }[];
  sdk: {
    totals: { cost_usd?: number; requests?: number; input_tokens?: number; output_tokens?: number; cache_read_tokens?: number; retries?: number; unpriced_requests?: number };
    by_model: AiRow[]; by_app: AiRow[]; by_team: AiRow[]; by_feature: AiRow[];
  };
}
export interface AiChanges { days: number; now: number; before: number; change: number; sentences: string[] }
export interface AiWaste { kind: string; key: string; title: string; detail: string; saving_monthly: number; requests: number }
export interface TeamRow { id: string; name: string; cost_key: string; description: string; members: { user_id: string; email: string; name: string | null }[] }
export interface OrgDomainRow { id: string; domain: string; verified_at: string | null; last_checked_at: string | null; last_error: string | null; record: { name: string; type: string; value: string } }
export interface ElevatedAccessRow {
  id: string; user_id: string; user: string | null; role: string; minutes: number; reason: string;
  status: "requested" | "active" | "denied" | "revoked" | "expired"; decided_by: string | null; starts_at: string | null; expires_at: string | null; created_at: string;
}
export interface FixRow {
  id: string; finding_id: string | null; finding: string | null; action: string; label: string; resource_id: string; region: string;
  savings_monthly: number; status: string; requested_by: string | null; approved_by: string | null; executed_at: string | null;
  result: { error?: string; snapshot_id?: string }; created_at: string;
}
export interface WriteAccess { connection_id: string; name: string; account_id: string | null; enabled_by: string | null; enabled_at: string; expires_at: string; auto_request: boolean }
export interface FixesStatus {
  enabled: boolean; role_name: string; protect_tag: string; cloudformation?: string;
  settings: { role_arn: string; expires_at: string; max_actions_per_day: number; spend_limit_monthly: number; enabled_by: string } | null;
}
export interface CustomRoleRow { id: string; name: string; description: string; permissions: string[]; accounts: string[]; teams: string[]; members: number }
export interface ScimStatus { enabled: boolean; token_hint: string | null; default_role: string; base_url: string; token?: string }
export interface SetupStep { key: string; title: string; why: string; done: boolean; link: string; optional?: boolean }
export interface InboxItem {
  id: string; event: string; title: string; text: string; link: string | null;
  severity: "alert" | "resolved" | "info"; organization_id: string; organization: string;
  created_at: string; read_at: string | null;
}
export interface InboxPage { items: InboxItem[]; unread: number; next_before: string | null }
export interface NotificationPref { event: string; label: string; inbox: boolean; email: boolean }
export interface FirstSavings {
  stage: "connect" | "importing" | "analysing" | "ready" | "none_found";
  count: number; savings_monthly: number; currency: string;
  top: { id: string; title: string; savings_monthly: number; currency: string }[];
  started_at: string | null; finished_at: string | null;
}
export interface OrgPlan {
  plan: { id: string; name: string };
  source: "local" | "demo" | "chosen" | "trial" | "default" | "free_tier";
  placeholder: boolean;
  features: { id: string; label: string; included: boolean }[];
  limits: { key: string; label: string; limit: number | null; used: number | null }[];
}
export interface SubscriptionInfo {
  status: "none" | "trialing" | "active" | "past_due" | "ended";
  plan: { id: string; name: string };
  payments_available: boolean;
  choices: { id: string; name: string }[];
  interval?: "monthly" | "annual";
  provider?: string;
  trial_ends_at?: string | null;
  current_period_end?: string;
  cancel_at_period_end?: boolean;
  pending_plan?: { id: string; name: string } | null;
}
export interface UsageDay {
  day: string; members: number; connections: number; clusters: number;
  cloud_spend_monthly_usd: number; api_calls: number; ai_summaries: number;
}
export interface OrgUsage { now: Record<string, number | null>; days: UsageDay[] }
export interface LicenceStatus {
  deployment_mode: "local" | "saas" | "self_hosted";
  state: "none" | "invalid" | "valid" | "grace" | "expired";
  problem: string | null;
  warning: string | null;
  id?: string; customer?: string; plan?: string; expires_at?: string; days_left?: number;
  grace_ends_at?: string; grace_days_left?: number | null;
  limits?: Record<string, number | null>; features?: string[];
}
export interface TrialStatus { status: "none" | "active" | "ending" | "ended"; ends_at: string | null; days_left: number | null }
export interface SetupChecklist { steps: SetupStep[]; done: number; total: number; next: string | null; hidden: boolean }
export interface OrgApiKeyRow {
  id: string; name: string; scope: "read" | "write" | "admin"; key_hint: string; created_by: string | null;
  created_at: string; expires_at: string | null; last_used_at: string | null; key: string | null; can_investigate?: boolean;
}
export interface AiUsageKeyRow { id: string; name: string; key_hint: string; last_used_at: string | null; created_at: string; key: string | null }

export interface NotificationChannel {
  id: string;
  kind: "slack" | "teams";
  name: string;
  url_hint: string;
  events: string[];
  enabled: boolean;
  last_sent_at: string | null;
  last_error: string | null;
}

export interface AgentTask {
  id: string;
  kind: "logs" | "describe" | "promql";
  params: Record<string, unknown>;
  status: "queued" | "running" | "done" | "failed" | "expired";
  result: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface FirstScan {
  at: string;
  workloads: number;
  pods: number;
  nodes: number;
  failing: number;
  warnings: number;
  findings: { namespace: string; kind: string; name: string; health: string; summary: string; cause: string | null; first_fix: string | null }[];
}

export interface ClusterPrices {
  currency: string;
  cpu_hour: number;
  gib_hour: number;
  node_hour: number;
  spot_discount_pct: number;
  always: boolean;
}

export interface MetricQuery {
  id: string;
  name: string;
  promql: string;
  unit: string;
  enabled: boolean;
  last_run_at: string | null;
  last_error: string | null;
  latest: { series: string; labels: Record<string, string>; value: number; at: string }[];
}

export interface Finding {
  id: string;
  detector: string;
  category: string;
  title: string;
  description: string;
  provider: string | null;
  account_id: string | null;
  region: string | null;
  service: string | null;
  resource_type: string | null;
  resource_id: string | null;
  savings_monthly: number;
  currency: string;
  risk: "low" | "medium" | "high";
  effort: "low" | "medium" | "high";
  confidence?: "low" | "medium" | "high";
  steps: string[];
  commands: string[];
  evidence: Record<string, unknown>;
  status: "open" | "approved" | "applied" | "verified" | "dismissed" | "gone";
  status_note: string | null;
  owner_id?: string | null;
  team?: string | null;
  owner_source?: "rule" | "person" | null;
  approved_at?: string | null;
  applied_at?: string | null;
  verified_at?: string | null;
  realised_monthly?: number | null;
  measurement?: { before_daily?: number; after_daily?: number | null; days_after?: number };
  first_seen_at: string;
  last_seen_at: string;
  score: number;
  overlaps: boolean;
}

export interface FindingsPage {
  summary: { count: number; savings_monthly: number; overlapping: number; by_category: Record<string, { count: number; savings_monthly: number }>;
    realised_monthly?: number; verified_count?: number };
  items: Finding[];
  detectors: { name: string; category: string; title: string }[];
}

export type AuditStatus = "saving" | "more_data" | "manual" | "fine";

export interface AuditRun {
  id: string;
  started_at: string;
  finished_at: string | null;
  status: "running" | "done" | "failed";
  trigger: "manual" | "scheduled";
  requested_by: string | null;
  currency: string;
  summary: {
    checks: number;
    counts: Record<AuditStatus, number>;
    by_area: Record<string, Record<AuditStatus, number>>;
    savings_monthly: number;
    uncovered_spend_monthly: number;
    changed: number;
    areas: Record<string, string>;
  };
}

export interface AuditResult {
  check_id: string;
  area: string;
  service: string;
  title: string;
  status: AuditStatus;
  reason: string;
  spend_monthly: number;
  savings_monthly: number;
  finding_count: number;
  details: { previous_status?: AuditStatus; resources?: number; billing_services?: string[] };
}

export interface CommitmentItem {
  id: string; kind: string; commitment_id: string; account_id: string | null; region: string | null;
  description: string; count: number; hourly_commitment: number | null; upfront: number | null;
  recurring_hourly: number | null; currency: string; term_years: number | null; payment_option: string | null;
  offering_class: string | null; starts_at: string | null; ends_at: string | null; state: string;
  utilization_pct: number | null; days_left: number | null;
}

export interface ContractIn {
  name: string; provider: "aws" | "azure" | "gcp" | "other"; kind: "edp" | "private_pricing" | "marketplace" | "other";
  commitment_amount: number; currency: string; discount_pct: number | null; starts_on: string; ends_on: string; notes: string;
}

export interface ContractOut extends ContractIn {
  id: string;
  progress: { spent: number; progress_pct: number | null; expected_pct: number; forecast: number; shortfall: number; days_left: number };
}

export interface PurchasePlan {
  currency: string;
  settings: { percentile: number; term: string; payment: string; rungs: number; interval_months: number; days: number };
  savings_plan: {
    status: "ok" | "no_data"; reason?: string; percentile?: number; steady_on_demand_hourly?: number;
    pending_changes_monthly?: number; after_changes_on_demand_hourly?: number; discount?: number; discount_source?: string;
    total_hourly_commitment?: number; monthly_saving?: number;
    window?: { start: string; end: string; days: number };
    ladder?: { date: string; hourly_commitment: number; monthly_saving: number | null; reason: string }[];
  };
  reservations: {
    service: string; region: string; instance_type: string; count: number; running_peak: number;
    on_demand_hourly: number; discount: number; monthly_saving: number; ladder: { when: string; count: number }[];
  }[];
}

export interface PurchasePolicy {
  max_monthly_commitment: number | null; currency: string; max_term: "1yr" | "3yr";
  allowed_payments: string[]; max_coverage_pct: number; preview_days: number;
}

export interface PurchaseIn {
  kind: "savings_plan" | "reservation"; description: string; hourly_commitment?: number | null;
  instance_type?: string | null; count?: number | null; service?: string | null; region?: string | null;
  term: "1yr" | "3yr"; payment: string; monthly_cost: number; estimated_monthly_saving: number; planned_for: string; notes?: string;
}

export interface PlannedPurchase extends PurchaseIn {
  id: string; status: "planned" | "approved" | "cancelled" | "purchased"; currency: string;
  commitment_id: string | null; purchased_on: string | null;
  verification: { month: string; saving: number; estimated: number; utilization_pct: number | null }[];
}

export interface FlowLogSource { type: "s3" | "cloudwatch"; bucket?: string | null; prefix?: string; log_group?: string | null }

export interface FlowLogSettings {
  enabled: boolean; sources: FlowLogSource[]; last_run: string | null; lines: number | null;
  errors: Record<string, string>; policy_statements: Record<string, unknown>[];
}

export interface NetworkAttribution {
  computed_at: string | null; currency: string | null; categories: Record<string, string>;
  items: { category: string; owner: string; owner_kind: string; team: string | null; gb: number; share_pct: number; cost_monthly: number; az: string | null }[];
  teams: { team: string; cost_monthly: number }[];
}

export interface SuppressionRule {
  id: string; detector: string; check: string; scope: string; value: string; reason: string; reason_label: string;
  note: string; expires_at: string | null; created_at: string; findings: number;
}

export interface NoiseRow {
  detector: string; title: string; total: number; open: number; dismissed: number; acted: number;
  dismissed_pct: number | null; reasons: Record<string, number>; noisy: boolean;
}

export interface OwnerRuleIn {
  name: string; priority: number; kind: "account" | "tag" | "vtag" | "service" | "namespace" | "category";
  key: string; value: string; team: string | null; owner_id: string | null;
}

export interface OwnerRule extends OwnerRuleIn { id: string; findings: number; savings_monthly: number }

export interface QuickWins {
  currency: string | null;
  buckets: { key: string; label: string; why: string; count: number; savings_monthly: number;
    items: { id: string; title: string; savings_monthly: number; risk: string; effort: string; confidence: string; team: string | null }[] }[];
}

export interface KeptSummary {
  currency: string | null; latest_month: string | null; kept_latest_month: number; kept_total: number;
  months: { month: string; kept: number }[];
  regressions: { id: string; title: string; regressed_at: string }[];
}

export interface RoiPack {
  organisation: string; currency: string; period: { start: string; end: string; months: number };
  verified_total: number; estimated_monthly: number; product_cost: number | null; product_currency: string;
  roi_multiple: number | null; net: number | null;
  verified: { kind: string; title: string; resource: string | null; confirmed: string | null; before_daily: number | null;
    after_daily: number | null; months: number; monthly: number; total: number; status: string }[];
  estimated: { title: string; status: string; monthly: number }[];
  notes: string[];
}

export interface RoiSettings { monthly_price: number | null; currency: string; since: string | null }

export interface UpgradeOverview {
  staying_monthly: number;
  items: { id: string; title: string; detector: string; staying_monthly: number; currency: string; basis: string | null;
    standard_end: string | null; upgrade_to: string | null; effort: string }[];
  coming_up: { resource: string; kind: string; version: string; ends: string; upgrade_to: string; estimated_monthly_usd: number | null }[];
}

export interface BudgetIn {
  name: string; parent_id: string | null; amount: number; currency: string; period: "monthly" | "quarterly" | "annual";
  metric: "effective" | "billed"; filters: Record<string, string[]>; thresholds: number[]; alert_on_forecast: boolean;
  recipients: string[];
}

export interface BudgetStatus extends BudgetIn {
  id: string; period_key: string; period_start: string; period_end: string; spent: number; forecast: number;
  spent_pct: number | null; forecast_pct: number | null; children: string[]; allocated_to_children: number; over_allocated: boolean;
}

export interface GovPolicy {
  id: string; name: string; kind: string; kind_label: string; params: Record<string, unknown>; resource_types: string[];
  severity: "info" | "warning" | "critical"; enabled: boolean; open: number; monthly_cost: number;
}

export interface GovViolation {
  id: string; policy_id: string; policy: string; severity: string; resource: string; resource_type: string; name: string | null;
  account_id: string | null; region: string | null; detail: string; monthly_cost: number | null; status: string; note: string | null;
}

export interface AllocRule {
  id: string; name: string; position: number; dimension: string; filters: Record<string, string[]>;
  method: "proportional" | "even" | "fixed"; targets: string[]; percents: Record<string, number>;
}

export interface Showback {
  month: string; dimension: string; currency: string; total: number; allocated_total: number; adds_up: boolean;
  rules: { name: string; pool: number; method: string }[];
  rows: { value: string; direct: number; shared: Record<string, number>; shared_total: number; total: number }[];
}

export interface MonthStatement {
  month: string; currency: string; total: number;
  by_category: { key: string; cost: number }[]; by_account: { key: string; cost: number }[]; by_service: { key: string; cost: number }[];
  invoices: { id: string; invoice_id: string; provider: string; billing_account_id: string | null; amount: number; currency: string;
    billed: number; difference: number; status: "matched" | "mismatch"; source: string }[];
  closed: { at: string; note: string } | null;
}

export interface ReportScheduleRow {
  id: string; name: string; kind: string; kind_label: string; frequency: "daily" | "weekly" | "monthly"; weekday: number; day: number;
  format: "pdf" | "csv"; recipients: string[]; params: Record<string, string>; enabled: boolean; last_sent_at: string | null; last_error: string | null;
}

export interface UnitMetricRow {
  id: string; name: string; unit: string; description: string; filters: Record<string, string[]>; metric: string; target_per_unit: number | null;
}

export interface UnitReport {
  metric: { id: string; name: string; unit: string; target_per_unit: number | null };
  currency: string; per_unit: number | null; change_pct: number | null; days_with_units: number;
  days: { date: string; cost: number; units: number | null; per_unit: number | null }[];
  months: { month: string; cost: number; units: number; per_unit: number | null }[];
}

export interface ExecutiveSummary {
  currency: string; as_of: string | null;
  spend: { month_to_date: number; forecast: number; last_month: number; change_vs_last_month_pct: number | null };
  movers: { key: string; label?: string; current: number; previous: number; change: number }[];
  budgets: { count: number; at_risk: { name: string; forecast_pct: number | null }[] };
  savings: { open_monthly: number; open_count: number; this_week_monthly: number; kept_latest_month: number; kept_month: string | null };
  commitments: { coverage_pct: number | null; utilization_pct: number | null };
  unit_cost: { name: string; unit: string; per_unit: number | null; change_pct: number | null } | null;
  policy_violations: number; anomalies_this_month: number;
}

export interface ReliabilityMatch {
  kind: "k8s_workload" | "k8s_namespace" | "aws_resource" | "aws_tag";
  cluster_id?: string | null;
  namespace?: string | null;
  workload?: string | null;
  resource_id?: string | null;
  key?: string | null;
  value?: string | null;
}

export interface ReliabilityTargetIn {
  name: string;
  match: ReliabilityMatch;
  tier: "critical" | "standard" | "best_effort";
  availability_pct: number;
  headroom_pct: number;
  max_utilization_pct: number;
  min_replicas: number;
  notes: string;
}

export interface ReliabilityTarget extends ReliabilityTargetIn {
  id: string;
}

export interface CommitmentAccount {
  connection_id: string | null;
  name: string;
  taken_at: string;
  savings_plans?: { utilization_pct: number; coverage_pct: number | null; unused_commitment: number; net_savings: number;
    total_commitment: number };
  reservations?: { utilization_pct: number; coverage_pct: number | null; net_savings: number };
  sp_recommendations?: { plan_type: string; hourly_commitment: number; monthly_savings: number; currency: string }[];
  ri_recommendations?: { service: string; instance_type: string; count: number; monthly_savings: number }[];
  errors?: string[];
}

export interface NetworkCosts {
  currency: string;
  days: number;
  total: number;
  start?: string;
  end?: string;
  categories: { key: string; label: string; cost: number; share_pct: number | null }[];
  daily: { periods: string[]; series: { key: string; label: string; total: number; values: number[] }[] };
  top: { category: string; label: string; resource_id: string | null; service: string; account: string | null; region: string | null; cost: number }[];
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
  teams: () => apiFetch<TeamRow[]>("/api/v1/org/teams"),
  createTeam: (body: { name: string; cost_key?: string; members: string[] }) => apiFetch<TeamRow>("/api/v1/org/teams", { method: "POST", ...json(body) }),
  updateTeam: (id: string, body: { members?: string[]; name?: string; cost_key?: string }) =>
    apiFetch<TeamRow>(`/api/v1/org/teams/${id}`, { method: "PATCH", ...json(body) }),
  deleteTeam: (id: string) => apiFetch<null>(`/api/v1/org/teams/${id}`, { method: "DELETE" }),
  domains: () => apiFetch<OrgDomainRow[]>("/api/v1/org/domains"),
  domainPolicy: () => apiFetch<{ enforce_sso: boolean; auto_join: boolean; auto_join_role: string }>("/api/v1/org/domain-policy"),
  setDomainPolicy: (body: { enforce_sso: boolean; auto_join: boolean; auto_join_role: string }) =>
    apiFetch<{ enforce_sso: boolean; auto_join: boolean; auto_join_role: string }>("/api/v1/org/domain-policy", { method: "PUT", ...json(body) }),
  addDomain: (domain: string) => apiFetch<OrgDomainRow>("/api/v1/org/domains", { method: "POST", ...json({ domain }) }),
  verifyDomain: (id: string) => apiFetch<OrgDomainRow>(`/api/v1/org/domains/${id}/verify`, { method: "POST" }),
  removeDomain: (id: string) => apiFetch<null>(`/api/v1/org/domains/${id}`, { method: "DELETE" }),
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
  setBedrockLogs: (id: string, body: { bedrock_log_bucket: string; bedrock_log_prefix: string; bedrock_rules: { match: string; app: string; team: string }[] }) =>
    apiFetch<CloudConnection>(`/api/v1/connections/${id}`, { method: "PATCH", ...json(body) }),
  importBedrockLogs: (id: string) =>
    apiFetch<{ files?: number; events?: number; unpriced?: number; error?: string }>(`/api/v1/connections/${id}/bedrock-logs/import`, { method: "POST" }),
  setupHelp: (provider: "azure" | "gcp", params: Record<string, string> = {}) =>
    apiFetch<SetupHelp>(`/api/v1/connections/setup-help/${provider}?${new URLSearchParams(params)}`),
  connectionSetupHelp: (id: string) => apiFetch<SetupHelp>(`/api/v1/connections/${id}/setup-help`),
  createSaas: (body: { provider: SaasProvider; name: string; settings: Record<string, string>; secret: Record<string, string> }) =>
    apiFetch<CloudConnection>("/api/v1/connections/saas", { method: "POST", ...json(body) }),
  createGcp: (body: GcpCreate) => apiFetch<CloudConnection>("/api/v1/connections/gcp", { method: "POST", ...json(body) }),
  createAzure: (body: AzureCreate) => apiFetch<CloudConnection>("/api/v1/connections/azure", { method: "POST", ...json(body) }),
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
  costProviders: (q: CostQuery = {}) => apiFetch<ProvidersView>(`/api/v1/costs/providers${costQuery(q)}`),
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
  k8sCosts: (params: { group_by: string; days: number; cluster_id?: string; namespace?: string; currency?: string }) =>
    apiFetch<K8sCosts>(`/api/v1/k8s/costs?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]))}`),
  rightsizing: (params: { cluster_id?: string; currency?: string }) =>
    apiFetch<Rightsizing>(`/api/v1/k8s/rightsizing?${new URLSearchParams(Object.entries(params).filter(([, v]) => v).map(([k, v]) => [k, String(v)]))}`),
  k8sWorkloads: (clusterId: string) => apiFetch<K8sWorkload[]>(`/api/v1/k8s/workloads?cluster_id=${clusterId}`),
  runInvestigation: (target: InvestigationTarget) =>
    apiFetch<InvestigationResult>("/api/v1/k8s/investigations/run", { method: "POST", ...json(target) }),
  saveInvestigation: (target: InvestigationTarget) =>
    apiFetch<SavedInvestigation>("/api/v1/k8s/investigations", { method: "POST", ...json(target) }),
  investigations: () => apiFetch<SavedInvestigation[]>("/api/v1/k8s/investigations"),
  investigation: (id: string) => apiFetch<SavedInvestigation>(`/api/v1/k8s/investigations/${id}`),
  refreshInvestigation: (id: string) => apiFetch<SavedInvestigation>(`/api/v1/k8s/investigations/${id}/refresh`, { method: "POST" }),
  workIntegrations: () => apiFetch<WorkIntegration[]>("/api/v1/org/work-integrations"),
  createWorkIntegration: (body: WorkIntegrationIn) =>
    apiFetch<WorkIntegration>("/api/v1/org/work-integrations", { method: "POST", ...json(body) }),
  updateWorkIntegration: (id: string, body: { enabled?: boolean; name?: string; token?: string }) =>
    apiFetch<WorkIntegration>(`/api/v1/org/work-integrations/${id}`, { method: "PATCH", ...json(body) }),
  deleteWorkIntegration: (id: string) => apiFetch<null>(`/api/v1/org/work-integrations/${id}`, { method: "DELETE" }),
  findingLinks: (id: string) => apiFetch<FindingLink[]>(`/api/v1/findings/${id}/links`),
  openFindingLink: (id: string, integrationId: string) =>
    apiFetch<FindingLink>(`/api/v1/findings/${id}/links`, { method: "POST", ...json({ integration_id: integrationId }) }),
  outgoingWebhooks: () => apiFetch<OutgoingWebhook[]>("/api/v1/org/outgoing-webhooks"),
  createOutgoingWebhook: (body: { name: string; url: string; events?: string[] }) =>
    apiFetch<OutgoingWebhook>("/api/v1/org/outgoing-webhooks", { method: "POST", ...json(body) }),
  updateOutgoingWebhook: (id: string, body: { enabled?: boolean; name?: string; events?: string[] }) =>
    apiFetch<OutgoingWebhook>(`/api/v1/org/outgoing-webhooks/${id}`, { method: "PATCH", ...json(body) }),
  deleteOutgoingWebhook: (id: string) => apiFetch<null>(`/api/v1/org/outgoing-webhooks/${id}`, { method: "DELETE" }),
  testOutgoingWebhook: (id: string) => apiFetch<WebhookDeliveryRow>(`/api/v1/org/outgoing-webhooks/${id}/test`, { method: "POST" }),
  rotateOutgoingWebhook: (id: string) => apiFetch<OutgoingWebhook>(`/api/v1/org/outgoing-webhooks/${id}/rotate-secret`, { method: "POST" }),
  webhookDeliveries: (id: string) => apiFetch<WebhookDeliveryRow[]>(`/api/v1/org/outgoing-webhooks/${id}/deliveries`),
  aiSummary: (days = 30) => apiFetch<AiSummary>(`/api/v1/ai/summary?days=${days}`),
  aiChanges: (days = 7) => apiFetch<AiChanges>(`/api/v1/ai/changes?days=${days}`),
  aiWaste: () => apiFetch<AiWaste[]>("/api/v1/ai/waste"),
  elevatedAccess: () => apiFetch<ElevatedAccessRow[]>("/api/v1/org/elevated-access"),
  requestAccess: (body: { role: string; minutes: number; reason: string }) =>
    apiFetch<ElevatedAccessRow>("/api/v1/org/elevated-access", { method: "POST", ...json(body) }),
  decideAccess: (id: string, action: "approve" | "deny" | "revoke") =>
    apiFetch<ElevatedAccessRow>(`/api/v1/org/elevated-access/${id}/${action}`, { method: "POST" }),
  portfolio: () => apiFetch<PortfolioRow[]>("/api/v1/partner/portfolio"),
  partnerClients: () => apiFetch<PartnerClient[]>("/api/v1/partner/clients"),
  createPartnerClient: (body: { name: string; markup_pct: number }) => apiFetch<{ id: string }>("/api/v1/partner/clients", { method: "POST", ...json(body) }),
  setClientMarkup: (id: string, markup_pct: number) =>
    apiFetch<{ markup_pct: number }>(`/api/v1/partner/clients/${id}`, { method: "PATCH", ...json({ markup_pct }) }),
  rebill: (id: string, month: string) => apiFetch<Rebill>(`/api/v1/partner/clients/${id}/rebill?month=${month}`),
  setBranding: (body: Branding) => apiFetch<Branding>("/api/v1/partner/branding", { method: "PUT", ...json(body) }),
  partnerLinkCode: () => apiFetch<{ code: string; valid_days: number }>("/api/v1/partner/link-codes", { method: "POST" }),
  branding: () => apiFetch<Branding | null>("/api/v1/org/branding"),
  uploadLogo: (file: File) => apiFetch<{ type: string; bytes: number }>("/api/v1/partner/logo", { method: "PUT", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } }),
  inviteInfo: (code: string) => apiFetch<{ partner: string; logo: string | null; valid_days: number }>(`/api/v1/partner-invites/${encodeURIComponent(code)}`),
  acceptInvite: (code: string, body: { organization_id?: string; new_name?: string }) =>
    apiFetch<{ organization_id: string; partner: string }>(`/api/v1/partner-invites/${encodeURIComponent(code)}/accept`, { method: "POST", ...json(body) }),
  applyPartner: (body: { company: string; website: string; message: string }) =>
    apiFetch<{ status: string }>("/api/v1/org/partner-application", { method: "POST", ...json(body) }),
  joinPartner: (code: string) => apiFetch<null>("/api/v1/org/partner", { method: "POST", ...json({ code }) }),
  leavePartner: () => apiFetch<null>("/api/v1/org/partner", { method: "DELETE" }),
  fixes: () => apiFetch<FixRow[]>("/api/v1/fixes"),
  writeAccess: () => apiFetch<WriteAccess[]>("/api/v1/org/write-access"),
  buyPurchase: (id: string) => apiFetch<FixRow>(`/api/v1/purchases/${id}/buy`, { method: "POST" }),
  requestFix: (findingId: string) => apiFetch<FixRow>(`/api/v1/findings/${findingId}/fix`, { method: "POST" }),
  decideFix: (id: string, action: "approve" | "reject") => apiFetch<FixRow>(`/api/v1/fixes/${id}/${action}`, { method: "POST" }),
  fixesStatus: (connectionId: string) => apiFetch<FixesStatus>(`/api/v1/connections/${connectionId}/fixes`),
  enableFixes: (connectionId: string, body: { role_arn: string; days: number; max_actions_per_day: number; spend_limit_monthly: number; auto_request: boolean; confirm: boolean }) =>
    apiFetch<FixesStatus>(`/api/v1/connections/${connectionId}/fixes`, { method: "POST", ...json(body) }),
  disableFixes: (connectionId: string) => apiFetch<null>(`/api/v1/connections/${connectionId}/fixes`, { method: "DELETE" }),
  customRoles: () => apiFetch<{ grantable: string[]; roles: CustomRoleRow[] }>("/api/v1/org/custom-roles"),
  createCustomRole: (body: { name: string; permissions: string[]; accounts: string[]; teams: string[] }) =>
    apiFetch<CustomRoleRow>("/api/v1/org/custom-roles", { method: "POST", ...json(body) }),
  deleteCustomRole: (id: string) => apiFetch<null>(`/api/v1/org/custom-roles/${id}`, { method: "DELETE" }),
  assignCustomRole: (userId: string, customRoleId: string | null) =>
    apiFetch<unknown>(`/api/v1/org/members/${userId}/custom-role`, { method: "PUT", ...json({ custom_role_id: customRoleId }) }),
  scim: () => apiFetch<ScimStatus>("/api/v1/org/scim"),
  enableScim: (default_role: string) => apiFetch<ScimStatus>("/api/v1/org/scim", { method: "POST", ...json({ default_role }) }),
  disableScim: () => apiFetch<null>("/api/v1/org/scim", { method: "DELETE" }),
  setup: () => apiFetch<SetupChecklist>("/api/v1/org/setup"),
  hideSetup: () => apiFetch<null>("/api/v1/org/setup/hide", { method: "POST" }),
  firstSavings: () => apiFetch<FirstSavings>("/api/v1/org/setup/first-savings"),
  trial: () => apiFetch<TrialStatus>("/api/v1/org/trial"),
  orgPlan: () => apiFetch<OrgPlan>("/api/v1/org/plan"),
  licence: () => apiFetch<LicenceStatus>("/api/v1/system/licence"),
  installLicence: (key: string) =>
    apiFetch<LicenceStatus>("/api/v1/system/licence", { method: "PUT", ...json({ key }) }),
  orgUsage: (days = 30) => apiFetch<OrgUsage>(`/api/v1/org/usage?days=${days}`),
  subscription: () => apiFetch<SubscriptionInfo>("/api/v1/org/subscription"),
  changePlan: (plan: string, interval: "monthly" | "annual") =>
    apiFetch<{ status: string; subscription: SubscriptionInfo }>("/api/v1/org/subscription/change", {
      method: "POST", ...json({ plan, interval }),
    }),
  cancelSubscription: () =>
    apiFetch<{ status: string; subscription: SubscriptionInfo }>("/api/v1/org/subscription/cancel", { method: "POST" }),
  resumeSubscription: () => apiFetch<SubscriptionInfo>("/api/v1/org/subscription/resume", { method: "POST" }),
  checkExport: (id: string) => apiFetch<ExportCheck>(`/api/v1/connections/${id}/export-check`, { method: "POST" }),
  notifications: (opts: { unreadOnly?: boolean; before?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (opts.unreadOnly) q.set("unread_only", "true");
    if (opts.before) q.set("before", opts.before);
    if (opts.limit) q.set("limit", String(opts.limit));
    const qs = q.toString();
    return apiFetch<InboxPage>(`/api/v1/me/notifications${qs ? `?${qs}` : ""}`);
  },
  unreadNotifications: () => apiFetch<{ unread: number }>("/api/v1/me/notifications/unread"),
  markNotificationRead: (id: string) =>
    apiFetch<null>(`/api/v1/me/notifications/${id}/read`, { method: "POST" }),
  markAllNotificationsRead: () =>
    apiFetch<{ unread: number }>("/api/v1/me/notifications/read-all", { method: "POST" }),
  notificationPrefs: () => apiFetch<NotificationPref[]>("/api/v1/me/notification-preferences"),
  saveNotificationPrefs: (prefs: NotificationPref[]) =>
    apiFetch<NotificationPref[]>("/api/v1/me/notification-preferences", { method: "PUT", ...json(prefs) }),
  apiKeys: () => apiFetch<OrgApiKeyRow[]>("/api/v1/org/api-keys"),
  createApiKey: (body: { name: string; scope: string; expires_at?: string; can_investigate?: boolean }) =>
    apiFetch<OrgApiKeyRow>("/api/v1/org/api-keys", { method: "POST", ...json(body) }),
  revokeApiKey: (id: string) => apiFetch<null>(`/api/v1/org/api-keys/${id}`, { method: "DELETE" }),
  aiUsageKeys: () => apiFetch<AiUsageKeyRow[]>("/api/v1/org/ai-usage-keys"),
  createAiUsageKey: (name: string) => apiFetch<AiUsageKeyRow>("/api/v1/org/ai-usage-keys", { method: "POST", ...json({ name }) }),
  revokeAiUsageKey: (id: string) => apiFetch<null>(`/api/v1/org/ai-usage-keys/${id}`, { method: "DELETE" }),
  notificationChannels: () => apiFetch<NotificationChannel[]>("/api/v1/org/notification-channels"),
  createNotificationChannel: (body: { kind: string; name: string; url: string }) =>
    apiFetch<NotificationChannel>("/api/v1/org/notification-channels", { method: "POST", ...json(body) }),
  updateNotificationChannel: (id: string, body: { enabled?: boolean; name?: string; events?: string[] }) =>
    apiFetch<NotificationChannel>(`/api/v1/org/notification-channels/${id}`, { method: "PATCH", ...json(body) }),
  deleteNotificationChannel: (id: string) => apiFetch<null>(`/api/v1/org/notification-channels/${id}`, { method: "DELETE" }),
  testNotificationChannel: (id: string) =>
    apiFetch<NotificationChannel>(`/api/v1/org/notification-channels/${id}/test`, { method: "POST" }),
  createAgentTask: (clusterId: string, kind: "logs" | "describe" | "promql", params: Record<string, unknown>) =>
    apiFetch<AgentTask>(`/api/v1/k8s/clusters/${clusterId}/tasks`, { method: "POST", ...json({ kind, params }) }),
  agentTask: (id: string) => apiFetch<AgentTask>(`/api/v1/k8s/tasks/${id}`),
  fixCheck: (body: { cluster_id: string; namespace: string; kind: string; name: string; fix_at?: string; change_id?: string }) =>
    apiFetch<FixCheck>("/api/v1/k8s/fix-check", { method: "POST", ...json(body) }),
  saveFixCheck: (investigationId: string, body: { fix_at?: string; change_id?: string }) =>
    apiFetch<FixCheck>(`/api/v1/k8s/investigations/${investigationId}/fix-check`, { method: "POST", ...json(body) }),
  diagnosisAccuracy: () => apiFetch<DiagnosisAccuracy>("/api/v1/k8s/accuracy"),
  closeInvestigation: (id: string, body: { real_cause: string; cause_note?: string; fix_note?: string }) =>
    apiFetch<SavedInvestigation>(`/api/v1/k8s/investigations/${id}/close`, { method: "POST", ...json(body) }),
  metricQueries: (clusterId: string) => apiFetch<MetricQuery[]>(`/api/v1/k8s/clusters/${clusterId}/metric-queries`),
  createMetricQuery: (clusterId: string, body: { name: string; promql: string; unit?: string }) =>
    apiFetch<MetricQuery>(`/api/v1/k8s/clusters/${clusterId}/metric-queries`, { method: "POST", ...json(body) }),
  updateMetricQuery: (id: string, body: { enabled?: boolean; name?: string }) =>
    apiFetch<MetricQuery>(`/api/v1/k8s/metric-queries/${id}`, { method: "PATCH", ...json(body) }),
  deleteMetricQuery: (id: string) => apiFetch<null>(`/api/v1/k8s/metric-queries/${id}`, { method: "DELETE" }),
  findings: (params: { status?: string; category?: string; owner?: string; team?: string }) =>
    apiFetch<FindingsPage>(`/api/v1/findings?${new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][])}`),
  updateFinding: (id: string, body: { status: Finding["status"]; note?: string }) =>
    apiFetch<Finding>(`/api/v1/findings/${id}`, { method: "PATCH", ...json(body) }),
  setFindingOwner: (id: string, owner_id: string | null) =>
    apiFetch<Finding>(`/api/v1/findings/${id}/owner`, { method: "PUT", ...json({ owner_id }) }),
  commentFinding: (id: string, text: string) =>
    apiFetch<{ id: string }>(`/api/v1/findings/${id}/comments`, { method: "POST", ...json({ text }) }),
  findingHistory: (id: string) =>
    apiFetch<{ at: string; kind: string; text: string; data: Record<string, unknown>; who: string }[]>(`/api/v1/findings/${id}/history`),
  measureFinding: (id: string) => apiFetch<Finding>(`/api/v1/findings/${id}/measure`, { method: "POST" }),
  runFindings: () => apiFetch<Record<string, unknown>>("/api/v1/findings/run", { method: "POST" }),
  commitments: () => apiFetch<{ accounts: CommitmentAccount[] }>("/api/v1/commitments"),
  refreshCommitments: () => apiFetch<{ snapshots: number; commitments: number }>("/api/v1/commitments/refresh", { method: "POST" }),
  commitmentInventory: () =>
    apiFetch<{ kinds: Record<string, string>; items: CommitmentItem[]; contracts: ContractOut[] }>("/api/v1/commitments/inventory"),
  commitmentPlan: (term: string, percentile: number) =>
    apiFetch<PurchasePlan>(`/api/v1/commitments/plan?term=${term}&percentile=${percentile}`),
  purchasePolicy: () => apiFetch<PurchasePolicy>("/api/v1/commitments/policy"),
  savePurchasePolicy: (body: PurchasePolicy) => apiFetch<PurchasePolicy>("/api/v1/commitments/policy", { method: "PUT", ...json(body) }),
  purchases: () => apiFetch<PlannedPurchase[]>("/api/v1/commitments/purchases"),
  planPurchase: (body: PurchaseIn) => apiFetch<PlannedPurchase>("/api/v1/commitments/purchases", { method: "POST", ...json(body) }),
  purchaseAction: (id: string, action: "approve" | "cancel") =>
    apiFetch<PlannedPurchase>(`/api/v1/commitments/purchases/${id}/${action}`, { method: "POST" }),
  markPurchased: (id: string, body: { commitment_id: string; purchased_on: string }) =>
    apiFetch<PlannedPurchase>(`/api/v1/commitments/purchases/${id}/purchased`, { method: "POST", ...json(body) }),
  flowLogs: (id: string) => apiFetch<FlowLogSettings>(`/api/v1/connections/${id}/flow-logs`),
  discoverFlowLogs: (id: string) =>
    apiFetch<{ sources: FlowLogSource[]; policy_statements: Record<string, unknown>[] }>(`/api/v1/connections/${id}/flow-logs/discover`, { method: "POST" }),
  saveFlowLogs: (id: string, body: { enabled: boolean; sources: FlowLogSource[] }) =>
    apiFetch<FlowLogSettings>(`/api/v1/connections/${id}/flow-logs`, { method: "PUT", ...json(body) }),
  runFlowLogs: (id: string) => apiFetch<{ rows: number; lines: number }>(`/api/v1/connections/${id}/flow-logs/run`, { method: "POST" }),
  networkAttribution: () => apiFetch<NetworkAttribution>("/api/v1/costs/network/attribution"),
  dismissFinding: (id: string, body: { reason: string; scope: string; note?: string; tag?: string }) =>
    apiFetch<{ rule_id: string; dismissed: number }>(`/api/v1/findings/${id}/dismiss`, { method: "POST", ...json(body) }),
  suppressions: () => apiFetch<SuppressionRule[]>("/api/v1/findings/suppressions"),
  deleteSuppression: (id: string) => apiFetch<{ reopened: number }>(`/api/v1/findings/suppressions/${id}`, { method: "DELETE" }),
  findingNoise: () => apiFetch<NoiseRow[]>("/api/v1/findings/noise"),
  ownerRules: () => apiFetch<OwnerRule[]>("/api/v1/findings/owner-rules"),
  createOwnerRule: (body: OwnerRuleIn) => apiFetch<OwnerRule>("/api/v1/findings/owner-rules", { method: "POST", ...json(body) }),
  deleteOwnerRule: (id: string) => apiFetch<{ changed: number }>(`/api/v1/findings/owner-rules/${id}`, { method: "DELETE" }),
  keptSavings: () => apiFetch<KeptSummary>("/api/v1/findings/kept"),
  roi: (start?: string, end?: string) =>
    apiFetch<RoiPack>(`/api/v1/roi?${new URLSearchParams(Object.entries({ start, end }).filter(([, v]) => v) as [string, string][])}`),
  roiSettings: () => apiFetch<RoiSettings>("/api/v1/roi/settings"),
  saveRoiSettings: (body: RoiSettings) => apiFetch<RoiSettings>("/api/v1/roi/settings", { method: "PUT", ...json(body) }),
  upgrades: () => apiFetch<UpgradeOverview>("/api/v1/findings/upgrades"),
  budgets: () => apiFetch<BudgetStatus[]>("/api/v1/budgets"),
  createBudget: (body: BudgetIn) => apiFetch<BudgetStatus>("/api/v1/budgets", { method: "POST", ...json(body) }),
  deleteBudget: (id: string) => apiFetch<null>(`/api/v1/budgets/${id}`, { method: "DELETE" }),
  govPolicies: () => apiFetch<GovPolicy[]>("/api/v1/governance/policies"),
  createGovPolicy: (body: { name: string; kind: string; params: Record<string, unknown>; severity: string }) =>
    apiFetch<GovPolicy>("/api/v1/governance/policies", { method: "POST", ...json(body) }),
  deleteGovPolicy: (id: string) => apiFetch<null>(`/api/v1/governance/policies/${id}`, { method: "DELETE" }),
  govViolations: () => apiFetch<GovViolation[]>("/api/v1/governance/violations"),
  exemptViolation: (id: string, note: string) =>
    apiFetch<{ status: string }>(`/api/v1/governance/violations/${id}/exempt`, { method: "POST", ...json({ note }) }),
  allocRules: () => apiFetch<AllocRule[]>("/api/v1/allocation/rules"),
  createAllocRule: (body: Omit<AllocRule, "id">) => apiFetch<AllocRule>("/api/v1/allocation/rules", { method: "POST", ...json(body) }),
  deleteAllocRule: (id: string) => apiFetch<null>(`/api/v1/allocation/rules/${id}`, { method: "DELETE" }),
  showback: (dimension: string, month: string) =>
    apiFetch<Showback>(`/api/v1/allocation/showback?${new URLSearchParams({ dimension, month })}`),
  statement: (month: string) => apiFetch<MonthStatement>(`/api/v1/statements/${month}`),
  closeMonth: (month: string, note: string) => apiFetch<MonthStatement>(`/api/v1/statements/${month}/close`, { method: "POST", ...json({ note }) }),
  reopenMonth: (month: string) => apiFetch<MonthStatement>(`/api/v1/statements/${month}/close`, { method: "DELETE" }),
  addInvoice: (month: string, body: { invoice_id: string; amount: number; currency: string; billing_account_id?: string }) =>
    apiFetch<unknown>(`/api/v1/statements/${month}/invoices`, { method: "POST", ...json(body) }),
  fetchInvoices: (month: string) => apiFetch<{ added: number; errors: string[] }>(`/api/v1/statements/${month}/invoices/fetch`, { method: "POST" }),
  reportSchedules: () => apiFetch<ReportScheduleRow[]>("/api/v1/reports/schedules"),
  createReportSchedule: (body: Omit<ReportScheduleRow, "id" | "kind_label" | "last_sent_at" | "last_error" | "enabled">) =>
    apiFetch<ReportScheduleRow>("/api/v1/reports/schedules", { method: "POST", ...json(body) }),
  deleteReportSchedule: (id: string) => apiFetch<null>(`/api/v1/reports/schedules/${id}`, { method: "DELETE" }),
  sendReportNow: (id: string) => apiFetch<{ sent: number }>(`/api/v1/reports/schedules/${id}/send`, { method: "POST" }),
  unitMetrics: () => apiFetch<UnitMetricRow[]>("/api/v1/unit-metrics"),
  createUnitMetric: (body: Omit<UnitMetricRow, "id" | "description" | "metric">) =>
    apiFetch<UnitMetricRow>("/api/v1/unit-metrics", { method: "POST", ...json(body) }),
  uploadUnitValues: (id: string, csvText: string) =>
    apiFetch<{ stored: number }>(`/api/v1/unit-metrics/${id}/values.csv`, { method: "POST", body: csvText, headers: { "Content-Type": "text/csv" } }),
  unitReport: (id: string) => apiFetch<UnitReport>(`/api/v1/unit-metrics/${id}/report`),
  leadershipReport: () => apiFetch<LeadershipReport>("/api/v1/reports/leadership"),
  executive: () => apiFetch<ExecutiveSummary>("/api/v1/roi/executive"),
  earlyWarnings: () => apiFetch<EarlyWarning[]>("/api/v1/costs/early-warnings"),
  quickWins: () => apiFetch<QuickWins>("/api/v1/findings/plan"),
  createContract: (body: ContractIn) => apiFetch<ContractOut>("/api/v1/commitments/contracts", { method: "POST", ...json(body) }),
  deleteContract: (id: string) => apiFetch<null>(`/api/v1/commitments/contracts/${id}`, { method: "DELETE" }),
  networkCosts: (days: number, currency?: string) =>
    apiFetch<NetworkCosts>(`/api/v1/costs/network?days=${days}${currency ? `&currency=${currency}` : ""}`),
  clusters: () => apiFetch<K8sCluster[]>("/api/v1/clusters"),
  createCluster: (name: string) => apiFetch<K8sClusterWithToken>("/api/v1/clusters", { method: "POST", ...json({ name }) }),
  updateCluster: (id: string, body: { name?: string; include_namespaces?: string[]; exclude_namespaces?: string[] }) =>
    apiFetch<K8sCluster>(`/api/v1/clusters/${id}`, { method: "PATCH", ...json(body) }),
  setClusterPrices: (id: string, prices: ClusterPrices) =>
    apiFetch<K8sCluster>(`/api/v1/clusters/${id}/prices`, { method: "PUT", ...json(prices) }),
  clearClusterPrices: (id: string) => apiFetch<K8sCluster>(`/api/v1/clusters/${id}/prices`, { method: "DELETE" }),
  revokeCluster: (id: string) => apiFetch<K8sCluster>(`/api/v1/clusters/${id}/revoke`, { method: "POST" }),
  rotateClusterToken: (id: string) => apiFetch<K8sClusterWithToken>(`/api/v1/clusters/${id}/rotate-token`, { method: "POST" }),
  deleteCluster: (id: string) => apiFetch<null>(`/api/v1/clusters/${id}`, { method: "DELETE" }),
  audits: () => apiFetch<AuditRun[]>("/api/v1/audits"),
  audit: (id: string) => apiFetch<{ run: AuditRun; results: AuditResult[] }>(`/api/v1/audits/${id}`),
  runAudit: () => apiFetch<AuditRun>("/api/v1/audits", { method: "POST" }),
  reliabilityTargets: () => apiFetch<ReliabilityTarget[]>("/api/v1/reliability-targets"),
  createReliabilityTarget: (body: ReliabilityTargetIn) =>
    apiFetch<ReliabilityTarget>("/api/v1/reliability-targets", { method: "POST", ...json(body) }),
  updateReliabilityTarget: (id: string, body: ReliabilityTargetIn) =>
    apiFetch<ReliabilityTarget>(`/api/v1/reliability-targets/${id}`, { method: "PUT", ...json(body) }),
  deleteReliabilityTarget: (id: string) => apiFetch<null>(`/api/v1/reliability-targets/${id}`, { method: "DELETE" }),
  alertSettings: () => apiFetch<AlertSettings>("/api/v1/org/alert-settings"),
  saveAlertSettings: (settings: AlertSettings) =>
    apiFetch<AlertSettings>("/api/v1/org/alert-settings", { method: "PUT", ...json(settings) }),
  askCosts: (question: string) => apiFetch<AskAnswer>("/api/v1/costs/ask", { method: "POST", ...json({ question }) }),
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
  createAlertReceiver: (body: { kind: "alertmanager" | "pagerduty" | "opsgenie"; name: string; cluster_id: string }) =>
    apiFetch<InboundWebhook & { setup: string }>("/api/v1/org/webhooks/alerts", { method: "POST", ...json(body) }),
  setWebhookSecret: (id: string, secret: string) =>
    apiFetch<null>(`/api/v1/org/webhooks/${id}/secret`, { method: "PUT", ...json({ secret }) }),
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
