import { Check, Minus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, type LicenceStatus, type OrgPlan, type SubscriptionInfo } from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const SOURCE: Record<OrgPlan["source"], string> = {
  local: "Local installation: every feature, no limits.",
  demo: "Demo organisation: every feature, read-only.",
  chosen: "Your organisation's plan.",
  trial: "Your free trial includes this plan.",
  default: "The plan every organisation starts on.",
  free_tier: "The free tier of a self-hosted installation without a licence.",
};

function amount(value: number | null): string {
  return value === null ? "Unlimited" : value.toLocaleString();
}

function day(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" }) : "";
}

function describe(sub: SubscriptionInfo): string {
  switch (sub.status) {
    case "trialing":
      return `Free trial of ${sub.plan.name} until ${day(sub.trial_ends_at)}.`;
    case "past_due":
      return `${sub.plan.name}: a payment failed and is being retried. Please check your payment method; if it is not paid within the grace period the organisation moves to the free plan (nothing is deleted).`;
    case "active": {
      const when = day(sub.current_period_end);
      if (sub.cancel_at_period_end) return `${sub.plan.name}, cancelled: it stays until ${when}, then the free plan.`;
      if (sub.pending_plan) return `${sub.plan.name} until ${when}, then ${sub.pending_plan.name}.`;
      return `${sub.plan.name}, billed ${sub.interval === "annual" ? "yearly" : "monthly"}; renews on ${when}.`;
    }
    case "ended":
      return `Your subscription has ended; you are on the ${sub.plan.name} plan. Everything you set up is kept.`;
    default:
      return `You are on the ${sub.plan.name} plan.`;
  }
}

/** Change plan, cancel or resume (B.2). Only owners manage billing. */
function SubscriptionCard({ onChanged }: { onChanged: () => void }) {
  const me = useMe();
  const { data, setData } = useLoad(() => api.subscription(), [me.active_organization_id]);
  const [plan, setPlan] = useState("");
  const [interval, setInterval] = useState<"monthly" | "annual">("monthly");
  const [busy, setBusy] = useState(false);
  if (!data || data.status === "none" || activeOrg(me)?.is_demo) return null;
  const isOwner = me.role === "owner";
  const live = ["trialing", "active", "past_due"].includes(data.status);

  async function run(action: () => Promise<SubscriptionInfo>, done: string) {
    setBusy(true);
    try {
      setData(await action());
      toast.success(done);
      onChanged();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Subscription</CardTitle>
        <CardDescription>{describe(data)}</CardDescription>
      </CardHeader>
      {isOwner && (
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-wrap items-end gap-2">
            <div className="grid gap-1">
              <Label htmlFor="plan-choice">Choose a plan</Label>
              <NativeSelect id="plan-choice" value={plan || data.plan.id} onChange={(e) => setPlan(e.target.value)}>
                {data.choices.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </NativeSelect>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="plan-interval">Billing</Label>
              <NativeSelect id="plan-interval" value={interval} onChange={(e) => setInterval(e.target.value as "monthly" | "annual")}>
                <option value="monthly">Monthly</option>
                <option value="annual">Yearly</option>
              </NativeSelect>
            </div>
            <Button disabled={busy} onClick={() => void run(async () => (await api.changePlan(plan || data.plan.id, interval)).subscription, "Plan change saved.")}>
              Change plan
            </Button>
          </div>
          {!data.payments_available && (
            <p className="text-sm text-muted-foreground">
              Moving to a smaller plan takes effect at the end of the period. Online payment is not available yet: to
              upgrade, contact us and we will set it up.
            </p>
          )}
          {live && (
            <div className="flex gap-2">
              {data.cancel_at_period_end ? (
                <Button variant="outline" disabled={busy} onClick={() => void run(() => api.resumeSubscription(), "Subscription resumed.")}>
                  Keep my subscription
                </Button>
              ) : (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm("Cancel the subscription? Your plan stays until the end of the period; nothing is deleted.")) {
                      void run(async () => (await api.cancelSubscription()).subscription, "Subscription cancelled.");
                    }
                  }}
                >
                  {data.status === "trialing" ? "End the trial now" : "Cancel subscription"}
                </Button>
              )}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}

const LICENCE_STATE: Record<LicenceStatus["state"], string> = {
  none: "No licence installed: this installation runs the free tier.",
  invalid: "The installed licence key cannot be used.",
  valid: "Licence active.",
  grace: "Licence expired: in its grace period.",
  expired: "Licence expired: this installation runs the free tier.",
};

/** Self-hosted licence (B.14): who it is for, until when, and installing a new key. */
function LicenceCard({ onChanged }: { onChanged: () => void }) {
  const me = useMe();
  const { data, setData } = useLoad(() => api.licence(), []);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  if (!data || data.deployment_mode !== "self_hosted") return null;

  async function install() {
    setBusy(true);
    try {
      setData(await api.installLicence(key.trim()));
      setKey("");
      toast.success("Licence installed.");
      onChanged();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Licence</CardTitle>
        <CardDescription>{LICENCE_STATE[data.state]}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        {data.warning && <p role="alert" className="rounded-md bg-warning/15 p-3">{data.warning}</p>}
        {data.customer && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
            <dt className="text-muted-foreground">Licensed to</dt><dd>{data.customer}</dd>
            <dt className="text-muted-foreground">Ends</dt><dd>{day(data.expires_at)}{data.state === "valid" ? ` (${data.days_left} days left)` : ""}</dd>
            {data.state === "grace" && (<><dt className="text-muted-foreground">Grace until</dt><dd>{day(data.grace_ends_at)}</dd></>)}
            <dt className="text-muted-foreground">Licence id</dt><dd className="font-mono text-xs">{data.id}</dd>
          </dl>
        )}
        {me.user.is_platform_admin ? (
          <div className="grid gap-2">
            <Label htmlFor="licence-key">Install a licence key</Label>
            <textarea
              id="licence-key"
              className="min-h-20 rounded-md border border-input bg-background p-2 font-mono text-xs"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="CCRv1.…"
            />
            <Button className="justify-self-start" disabled={busy || key.trim().length < 10} onClick={() => void install()}>
              Install licence
            </Button>
          </div>
        ) : (
          <p className="text-muted-foreground">An administrator of this installation installs licence keys.</p>
        )}
      </CardContent>
    </Card>
  );
}

/** Daily usage for the last 30 days (B.12), newest first. */
function UsageHistory() {
  const me = useMe();
  const { data } = useLoad(() => api.orgUsage(30), [me.active_organization_id]);
  if (!data || data.days.length === 0) return null;
  const rows = data.days.toReversed();
  return (
    <Card>
      <CardHeader>
        <CardTitle>Usage history</CardTitle>
        <CardDescription>Recorded every night for the day before.</CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <THead>
            <TR>
              <TH>Day</TH><TH className="text-right">People</TH><TH className="text-right">Connections</TH>
              <TH className="text-right">Clusters</TH><TH className="text-right">Spend, 30 days (USD)</TH>
              <TH className="text-right">API calls</TH><TH className="text-right">AI summaries</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((d) => (
              <TR key={d.day}>
                <TD>{day(d.day)}</TD>
                <TD className="text-right tabular-nums">{d.members}</TD>
                <TD className="text-right tabular-nums">{d.connections}</TD>
                <TD className="text-right tabular-nums">{d.clusters}</TD>
                <TD className="text-right tabular-nums">{Math.round(d.cloud_spend_monthly_usd).toLocaleString()}</TD>
                <TD className="text-right tabular-nums">{d.api_calls.toLocaleString()}</TD>
                <TD className="text-right tabular-nums">{d.ai_summaries.toLocaleString()}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

/** The organisation's plan: what it includes and usage against each limit (B.1). */
export function PlanTab() {
  const me = useMe();
  const { data, error, reload } = useLoad(() => api.orgPlan(), [me.active_organization_id]);
  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!data) return null;
  return (
    <div className="flex flex-col gap-4">
      <SubscriptionCard onChanged={() => void reload()} />
      {atLeast(me.role, "admin") && <LicenceCard onChanged={() => void reload()} />}
      <Card>
        <CardHeader>
          <CardTitle>{data.plan.name} plan</CardTitle>
          <CardDescription>
            {SOURCE[data.source]}
            {data.placeholder && " Plan names and limits are provisional."} Over a limit, CloudCostRadar stops adding more; it
            never removes what you already have.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <THead>
              <TR><TH>Limit</TH><TH className="text-right">Used</TH><TH className="text-right">Allowed</TH><TH className="w-40"><span className="sr-only">Usage</span></TH></TR>
            </THead>
            <TBody>
              {data.limits.map((l) => (
                <TR key={l.key}>
                  <TD>{l.label}</TD>
                  <TD className="text-right tabular-nums">{l.used === null ? "—" : l.used.toLocaleString()}</TD>
                  <TD className="text-right tabular-nums">{amount(l.limit)}</TD>
                  <TD>
                    {l.limit !== null && l.used !== null && (
                      <meter className="w-full" min={0} max={l.limit} high={l.limit * 0.8} value={Math.min(l.used, l.limit)} aria-label={`${l.label} used`} />
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      <UsageHistory />
      <Card>
        <CardHeader>
          <CardTitle>Included features</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="grid gap-2 sm:grid-cols-2">
            {data.features.map((f) => (
              <li key={f.id} className={`flex items-center gap-2 text-sm ${f.included ? "" : "text-muted-foreground"}`}>
                {f.included ? <Check className="size-4 text-primary" aria-label="Included" /> : <Minus className="size-4" aria-label="Not included" />}
                {f.label}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
