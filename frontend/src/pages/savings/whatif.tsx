import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, type WhatIfLever } from "@/lib/api";
import { money } from "@/lib/format";
import { useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

/** What-if planner (11.39): the monthly bill after chosen changes, on the bill's own rates. */
export function WhatIfCard() {
  const me = useMe();
  const [levers, setLevers] = useState<WhatIfLever[]>([]);
  const [term, setTerm] = useState<"" | "1yr" | "3yr">("");
  const plan = useLoad(() => api.whatIf(levers, term ? { term } : null), [me.active_organization_id, levers.join(), term]);
  const d = plan.data;
  if (!d || d.base_monthly <= 0) return null;
  const toggle = (id: WhatIfLever, on: boolean) => setLevers(on ? [...levers, id] : levers.filter((x) => x !== id));
  return (
    <Card>
      <CardHeader>
        <CardTitle>What if…</CardTitle>
        <CardDescription>Pick changes to see the monthly bill after them. Savings come from your own bill; a server counts once even if several changes touch it.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        <fieldset className="grid gap-2 text-sm" aria-label="Changes to include">
          {d.levers.filter((l) => l.count > 0).map((l) => (
            <label key={l.id} className="flex items-center gap-2">
              <input type="checkbox" checked={levers.includes(l.id)} onChange={(e) => toggle(l.id, e.target.checked)} />
              <span className="flex-1">{l.label} <span className="text-muted-foreground">({l.count})</span></span>
              <span className="tabular-nums">{money(l.monthly, d.currency)}</span>
            </label>
          ))}
          <div className="grid gap-1 pt-2">
            <Label htmlFor="whatif-sp">Then buy a Compute Savings Plan</Label>
            <NativeSelect id="whatif-sp" value={term} onChange={(e) => setTerm(e.target.value as "" | "1yr" | "3yr")}>
              <option value="">No</option>
              <option value="1yr">1 year, no upfront</option>
              <option value="3yr">3 years, no upfront</option>
            </NativeSelect>
            {d.commitment?.reason && <p className="text-xs text-muted-foreground">{d.commitment.reason}</p>}
          </div>
        </fieldset>
        <dl className="grid content-start gap-2 text-sm" aria-label="What-if result">
          <div className="flex justify-between"><dt>Now, last 30 days</dt><dd className="tabular-nums">{money(d.base_monthly, d.currency)}</dd></div>
          <div className="flex justify-between"><dt>Changes</dt><dd className="tabular-nums text-primary">−{money(d.levers_monthly, d.currency)}</dd></div>
          {d.commitment && d.commitment.monthly > 0 && (
            <div className="flex justify-between"><dt>Savings Plan on what is left</dt><dd className="tabular-nums text-primary">−{money(d.commitment.monthly, d.currency)}</dd></div>
          )}
          <div className="flex justify-between border-t pt-2 font-semibold"><dt>After</dt>
            <dd className="tabular-nums">{money(d.after_monthly, d.currency)}{d.saving_pct ? ` (−${d.saving_pct}%)` : ""}</dd></div>
        </dl>
      </CardContent>
    </Card>
  );
}
