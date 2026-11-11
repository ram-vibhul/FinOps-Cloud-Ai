import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type PlannedPurchase, type PurchasePolicy } from "@/lib/api";
import { money } from "@/lib/format";
import { useLoad } from "@/lib/use-load";

const PAYMENTS = { no_upfront: "No upfront", partial_upfront: "Partial upfront", all_upfront: "All upfront" } as const;
const STATUS: Record<PlannedPurchase["status"], "secondary" | "warning" | "default" | "outline"> = {
  planned: "warning", approved: "default", purchased: "secondary", cancelled: "outline",
};

export function PolicyCard({ canEdit }: { canEdit: boolean }) {
  const policy = useLoad(() => api.purchasePolicy(), []);
  return policy.data ? <PolicyForm initial={policy.data} canEdit={canEdit} /> : null;
}

function PolicyForm({ initial, canEdit }: { initial: PurchasePolicy; canEdit: boolean }) {
  const [p, setP] = useState<PurchasePolicy>(initial);

  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      setP(await api.savePurchasePolicy(p));
      toast.success("Purchase rules saved.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Purchase rules</CardTitle>
        <CardDescription>Every planned purchase must pass these before it can be approved.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={(e) => void save(e)} className="grid gap-3 sm:grid-cols-3" aria-label="Purchase rules">
          <fieldset disabled={!canEdit} className="contents">
            <div className="grid gap-1">
              <Label htmlFor="pp-limit">New commitments per month at most ({p.currency})</Label>
              <Input id="pp-limit" type="number" min={0} placeholder="No limit" value={p.max_monthly_commitment ?? ""}
                onChange={(e) => setP({ ...p, max_monthly_commitment: e.target.value === "" ? null : Number(e.target.value) })} />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="pp-term">Longest term</Label>
              <NativeSelect id="pp-term" value={p.max_term} onChange={(e) => setP({ ...p, max_term: e.target.value as "1yr" | "3yr" })}>
                <option value="1yr">1 year</option>
                <option value="3yr">3 years</option>
              </NativeSelect>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="pp-cover">Cover at most (% of steady usage)</Label>
              <Input id="pp-cover" type="number" min={10} max={100} value={p.max_coverage_pct}
                onChange={(e) => setP({ ...p, max_coverage_pct: Number(e.target.value) })} />
            </div>
            <div className="grid gap-1">
              <span className="text-sm font-medium">Payment options allowed</span>
              <div className="flex flex-wrap gap-3 text-sm">
                {Object.entries(PAYMENTS).map(([k, label]) => (
                  <label key={k} className="flex items-center gap-1">
                    <input type="checkbox" checked={p.allowed_payments.includes(k)}
                      onChange={(e) => setP({ ...p, allowed_payments: e.target.checked
                        ? [...p.allowed_payments, k] : p.allowed_payments.filter((x) => x !== k) })} />
                    {label}
                  </label>
                ))}
              </div>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="pp-preview">Preview period (days, at least 7)</Label>
              <Input id="pp-preview" type="number" min={7} max={60} value={p.preview_days}
                onChange={(e) => setP({ ...p, preview_days: Number(e.target.value) })} />
            </div>
            {canEdit && <div className="flex items-end"><Button type="submit">Save rules</Button></div>}
          </fieldset>
        </form>
      </CardContent>
    </Card>
  );
}

export function PurchasesCard({ canPlan, canApprove, reloadKey }: { canPlan: boolean; canApprove: boolean; reloadKey: number }) {
  const list = useLoad(() => api.purchases(), [reloadKey]);

  async function act(run: () => Promise<unknown>, done: string) {
    try {
      await run();
      toast.success(done);
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  function bought(x: PlannedPurchase) {
    const id = window.prompt("Savings Plan ARN or reservation ID from the AWS console:");
    if (!id) return;
    void act(() => api.markPurchased(x.id, { commitment_id: id.trim(), purchased_on: new Date().toISOString().slice(0, 10) }),
      "Marked as bought; savings are checked every month.");
  }

  const rows = list.data ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Planned purchases</CardTitle>
        <CardDescription>
          Plans are previewed for a week and approved by an admin. Then a person buys them in the AWS console, or, where an
          owner has turned on automatic fixes, CloudCostRadar buys an approved Savings Plan after a second person approves (within
          the monthly spend limit). CloudCostRadar measures the saving on the bill each month afterwards.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        {rows.length === 0 && !list.loading && (
          <p className="text-muted-foreground">Nothing planned. {canPlan ? "Use “Plan this” on a step of the purchase plan." : ""}</p>
        )}
        {rows.map((x) => (
          <div key={x.id} className="grid gap-1 rounded-lg border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-medium">{x.description} <Badge variant={STATUS[x.status]}>{x.status}</Badge></p>
              <div className="flex gap-1">
                {canApprove && x.status === "planned" && (
                  <Button size="sm" onClick={() => void act(() => api.purchaseAction(x.id, "approve"), "Approved.")}>Approve</Button>
                )}
                {canApprove && x.status === "approved" && <Button size="sm" variant="outline" onClick={() => bought(x)}>Mark as bought</Button>}
                {canPlan && x.status === "approved" && x.kind === "savings_plan" && (
                  <Button size="sm" variant="ghost" onClick={() => void act(() => api.buyPurchase(x.id), "Requested. A second person approves it under Savings → Automatic fixes.")}>Buy automatically</Button>
                )}
                {canPlan && (x.status === "planned" || x.status === "approved") && (
                  <Button size="sm" variant="ghost" onClick={() => void act(() => api.purchaseAction(x.id, "cancel"), "Cancelled.")}>Cancel</Button>
                )}
              </div>
            </div>
            <p className="text-muted-foreground">
              {new Date(x.planned_for).toLocaleDateString()} · {x.term} · {PAYMENTS[x.payment as keyof typeof PAYMENTS] ?? x.payment} ·
              {" "}{money(x.monthly_cost, x.currency)} a month committed · about {money(x.estimated_monthly_saving, x.currency)} saved
            </p>
            {x.verification.length > 0 && (
              <ul className="text-xs" aria-label={`Measured savings for ${x.description}`}>
                {x.verification.map((v) => (
                  <li key={v.month}>
                    {v.month}: saved {money(v.saving, x.currency)} (estimated {money(v.estimated, x.currency)})
                    {v.utilization_pct != null ? `, ${v.utilization_pct.toFixed(0)}% used` : ""}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
