import { FileSignature, Plus, RefreshCw, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, type CommitmentItem, type ContractIn, errorMessage } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { PolicyCard, PurchasesCard } from "./purchases";

function Expiry({ days }: { days: number | null }) {
  if (days == null) return <span className="text-muted-foreground">–</span>;
  const variant = days <= 30 ? "destructive" : days <= 90 ? "warning" : "secondary";
  return <Badge variant={variant}>{days <= 0 ? "ended" : `${days} days left`}</Badge>;
}

function price(c: CommitmentItem): string {
  if (c.hourly_commitment != null) return `${money(c.hourly_commitment, c.currency)}/hour`;
  const parts = [c.upfront ? `${money(c.upfront, c.currency)} upfront` : null,
    c.recurring_hourly ? `${money(c.recurring_hourly, c.currency)}/hour` : null].filter(Boolean);
  return parts.join(" + ") || "–";
}

const BLANK: ContractIn = {
  name: "", provider: "aws", kind: "edp", commitment_amount: 0, currency: "USD", discount_pct: null,
  starts_on: "", ends_on: "", notes: "",
};

function ContractForm({ onDone }: { onDone: () => void }) {
  const [c, setC] = useState<ContractIn>(BLANK);
  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      await api.createContract({ ...c, name: c.name.trim() });
      toast.success("Contract added.");
      onDone();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }
  return (
    <form onSubmit={(e) => void save(e)} aria-label="New contract" className="grid gap-3 rounded-lg border p-3 sm:grid-cols-3">
      <div className="grid gap-1 sm:col-span-2">
        <Label htmlFor="ct-name">Name</Label>
        <Input id="ct-name" required value={c.name} onChange={(e) => setC({ ...c, name: e.target.value })} placeholder="AWS EDP 2026–2027" />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="ct-kind">Type</Label>
        <NativeSelect id="ct-kind" value={c.kind} onChange={(e) => setC({ ...c, kind: e.target.value as ContractIn["kind"] })}>
          <option value="edp">Enterprise Discount Program</option>
          <option value="private_pricing">Private pricing</option>
          <option value="marketplace">Marketplace</option>
          <option value="other">Other</option>
        </NativeSelect>
      </div>
      <div className="grid gap-1">
        <Label htmlFor="ct-amount">Committed spend</Label>
        <Input id="ct-amount" type="number" min={1} required value={c.commitment_amount || ""}
          onChange={(e) => setC({ ...c, commitment_amount: Number(e.target.value) })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="ct-start">Starts</Label>
        <Input id="ct-start" type="date" required value={c.starts_on} onChange={(e) => setC({ ...c, starts_on: e.target.value })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="ct-end">Ends</Label>
        <Input id="ct-end" type="date" required value={c.ends_on} onChange={(e) => setC({ ...c, ends_on: e.target.value })} />
      </div>
      <div className="flex gap-2 sm:col-span-3">
        <Button type="submit">Save contract</Button>
        <Button type="button" variant="ghost" onClick={onDone}>Cancel</Button>
      </div>
    </form>
  );
}

function PlanCard({ canPlan, onPlanned }: { canPlan: boolean; onPlanned: () => void }) {
  const me = useMe();
  const [term, setTerm] = useState("1yr");
  const [pctl, setPctl] = useState(10);
  const plan = useLoad(() => api.commitmentPlan(term, pctl), [me.active_organization_id, term, pctl]);
  const p = plan.data;
  const sp = p?.savings_plan;
  const cur = p?.currency ?? "USD";

  async function planRung(r: { date: string; hourly_commitment: number; monthly_saving: number | null }) {
    const week = new Date(Date.now() + 8 * 86400000).toISOString().slice(0, 10);
    try {
      await api.planPurchase({
        kind: "savings_plan", description: `Compute Savings Plan ${money(r.hourly_commitment, cur)}/hour`,
        hourly_commitment: r.hourly_commitment, term: term as "1yr" | "3yr", payment: "no_upfront",
        monthly_cost: Math.round(r.hourly_commitment * 730 * 100) / 100, estimated_monthly_saving: r.monthly_saving ?? 0,
        planned_for: r.date > week ? r.date : week,
      });
      toast.success("Planned. It needs an admin's approval before anyone buys it.");
      onPlanned();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Purchase plan</CardTitle>
        <CardDescription>
          Sized on a low percentile of steady usage after the changes CostLens already suggests, and bought in steps so
          expiries are spread out. CostLens never buys anything: use this to plan purchases in the AWS console.
        </CardDescription>
        <div className="flex flex-wrap gap-2">
          <NativeSelect aria-label="Term" value={term} onChange={(e) => setTerm(e.target.value)}>
            <option value="1yr">1-year term</option>
            <option value="3yr">3-year term</option>
          </NativeSelect>
          <NativeSelect aria-label="Usage percentile" value={pctl} onChange={(e) => setPctl(Number(e.target.value))}>
            <option value={5}>Very cautious (5th percentile)</option>
            <option value={10}>Cautious (10th percentile)</option>
            <option value={25}>Balanced (25th percentile)</option>
          </NativeSelect>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 text-sm">
        {sp?.status === "no_data" && <p className="text-muted-foreground">{sp.reason}</p>}
        {sp?.status === "ok" && (
          <div className="grid gap-2">
            <p>
              <span className="font-medium">Compute Savings Plan: {money(sp.total_hourly_commitment ?? 0, cur)}/hour in total</span>
              {" "}— about {money(sp.monthly_saving ?? 0, cur)} a month.
            </p>
            <p className="text-muted-foreground">
              Steady On-Demand compute is {money(sp.steady_on_demand_hourly ?? 0, cur)}/hour (the {sp.percentile}th percentile
              of {sp.window?.days} days); pending CostLens changes remove {money(sp.pending_changes_monthly ?? 0, cur)} a month,
              leaving {money(sp.after_changes_on_demand_hourly ?? 0, cur)}/hour. Discount {(100 * (sp.discount ?? 0)).toFixed(0)}% ({sp.discount_source}).
            </p>
            <table className="w-full text-left">
              <thead className="text-xs text-muted-foreground">
                <tr className="border-b"><th scope="col" className="py-1 font-medium">When</th><th scope="col" className="py-1 font-medium">Buy</th><th scope="col" className="py-1 font-medium">Why</th><th scope="col"><span className="sr-only">Actions</span></th></tr>
              </thead>
              <tbody>
                {(sp.ladder ?? []).map((r) => (
                  <tr key={`${r.date}${r.reason}`} className="border-b last:border-0">
                    <td className="py-1">{new Date(r.date).toLocaleDateString()}</td>
                    <td className="py-1 tabular-nums">{money(r.hourly_commitment, cur)}/hour</td>
                    <td className="py-1 text-muted-foreground">{r.reason}</td>
                    <td className="py-1 text-right">
                      {canPlan && r.monthly_saving != null && (
                        <Button size="sm" variant="ghost" onClick={() => void planRung(r)}>Plan this</Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {(p?.reservations ?? []).length > 0 && (
          <div className="grid gap-1">
            <p className="font-medium">Reservations</p>
            <ul className="grid gap-1">
              {p!.reservations.map((r) => (
                <li key={`${r.service}${r.region}${r.instance_type}`}>
                  {r.count} × {r.instance_type} ({r.service.replace("Amazon ", "")}, {r.region}) — about {money(r.monthly_saving, cur)} a month;
                  <span className="text-muted-foreground"> peak {r.running_peak} running. {r.ladder.map((l) => `${l.count} ${l.when}`).join(", ")}.</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function CommitmentsPage() {
  const me = useMe();
  const demo = Boolean(activeOrg(me)?.is_demo);
  const canRefresh = atLeast(me.role, "admin") && !demo;
  const data = useLoad(() => api.commitmentInventory(), [me.active_organization_id]);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [planned, setPlanned] = useState(0);
  const canPlan = atLeast(me.role, "member") && !demo;
  const kinds = data.data?.kinds ?? {};

  async function refresh() {
    setBusy(true);
    try {
      const r = await api.refreshCommitments();
      toast.success(`${r.commitments} commitment(s) found.`);
      await data.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string, name: string) {
    if (!window.confirm(`Delete the contract ${name}?`)) return;
    try {
      await api.deleteContract(id);
      await data.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const items = data.data?.items ?? [];
  const contracts = data.data?.contracts ?? [];
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-muted-foreground"><Link to="/savings" className="underline underline-offset-4">Savings</Link> /</p>
          <h1 className="text-2xl font-semibold tracking-tight">Commitments</h1>
          <p className="text-sm text-muted-foreground">Every Savings Plan, reservation and negotiated contract, soonest to end first.</p>
        </div>
        {canRefresh && <Button variant="outline" disabled={busy} onClick={() => void refresh()}><RefreshCw /> Refresh from AWS</Button>}
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Savings Plans and reservations</CardTitle>
          <CardDescription>CostLens alerts your notification channels 90, 30 and 7 days before each one ends.</CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {items.length === 0 && !data.loading ? (
            <EmptyState icon={FileSignature} title="No commitments found">Savings Plans and reservations appear after the daily refresh.</EmptyState>
          ) : (
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="text-xs text-muted-foreground">
                <tr className="border-b">
                  <th scope="col" className="py-2 pr-3 font-medium">Commitment</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Price</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Term</th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">Used</th>
                  <th scope="col" className="py-2 font-medium">Ends</th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.id} className="border-b last:border-0">
                    <td className="py-2 pr-3">
                      <p className="font-medium">{kinds[c.kind] ?? c.kind}: {c.count > 1 ? `${c.count} × ` : ""}{c.description}</p>
                      <p className="text-xs text-muted-foreground">{[c.account_id, c.region, c.offering_class, c.state].filter(Boolean).join(" · ")}</p>
                    </td>
                    <td className="py-2 pr-3">{price(c)}</td>
                    <td className="py-2 pr-3">{c.term_years ? `${c.term_years} yr` : "–"}{c.payment_option ? `, ${c.payment_option}` : ""}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">
                      {c.utilization_pct == null ? "–" : (
                        <span className={c.utilization_pct < 90 ? "font-medium text-destructive" : undefined}>{c.utilization_pct.toFixed(0)}%</span>
                      )}
                    </td>
                    <td className="py-2">
                      <Expiry days={c.days_left} />
                      {c.ends_at && <span className="ml-2 text-xs text-muted-foreground">{new Date(c.ends_at).toLocaleDateString()}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
      <PlanCard canPlan={canPlan} onPlanned={() => setPlanned((n) => n + 1)} />
      <PurchasesCard canPlan={canPlan} canApprove={canRefresh} reloadKey={planned} />
      <PolicyCard canEdit={canRefresh} />
      <Card>
        <CardHeader>
          <CardTitle>Negotiated contracts</CardTitle>
          <CardDescription>Spend commitments agreed with a provider (EDP, private pricing). Progress uses the billed cost in the contract period.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          {canRefresh && !adding && <div><Button variant="outline" onClick={() => setAdding(true)}><Plus /> Add contract</Button></div>}
          {adding && <ContractForm onDone={() => { setAdding(false); void data.reload(); }} />}
          {contracts.length === 0 && !data.loading && <p className="text-sm text-muted-foreground">No contracts entered.</p>}
          {contracts.map((k) => (
            <div key={k.id} className="grid gap-2 rounded-lg border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-medium">{k.name} <Expiry days={k.progress.days_left} /></p>
                {canRefresh && <Button size="sm" variant="ghost" aria-label={`Delete ${k.name}`} onClick={() => void remove(k.id, k.name)}><Trash2 /></Button>}
              </div>
              <meter className="h-2 w-full" min={0} max={100} value={Math.min(k.progress.progress_pct ?? 0, 100)}
                aria-label={`${k.name} progress`} />
              <p className="text-sm text-muted-foreground">
                {money(k.progress.spent, k.currency)} of {money(k.commitment_amount, k.currency)} ({k.progress.progress_pct ?? 0}% spent,
                {" "}{k.progress.expected_pct}% of the term gone) · forecast {money(k.progress.forecast, k.currency)}
                {k.progress.shortfall > 0 && <span className="font-medium text-destructive"> · on course to fall short by {money(k.progress.shortfall, k.currency)}</span>}
              </p>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
