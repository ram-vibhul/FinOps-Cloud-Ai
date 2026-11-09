import { Download } from "lucide-react";
import { Link } from "react-router";
import { Term } from "@/components/term";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

function Tile({ label, value, note, to }: { label: string; value: string; note?: string; to?: string }) {
  const body = (
    <Card className="h-full">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">{value}</CardTitle>
        {note && <p className="text-xs text-muted-foreground">{note}</p>}
      </CardHeader>
    </Card>
  );
  return to ? <Link to={to} className="rounded-xl focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none">{body}</Link> : body;
}

export function ExecutivePage() {
  const me = useMe();
  const data = useLoad(() => api.executive(), [me.active_organization_id]);
  const report = useLoad(() => api.leadershipReport(), [me.active_organization_id]);
  const r = report.data;
  const e = data.data;
  const cur = e?.currency ?? "USD";
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Executive summary</h1>
        <p className="text-sm text-muted-foreground">{e?.as_of ? `Data through ${new Date(e.as_of).toLocaleDateString()}.` : "The numbers leadership asks about."}</p>
      </div>
      {e && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Tile label="Spend this month" value={money(e.spend.month_to_date, cur)} to="/costs"
              note={`Forecast ${money(e.spend.forecast, cur)}${e.spend.change_vs_last_month_pct != null ? ` (${e.spend.change_vs_last_month_pct > 0 ? "+" : ""}${e.spend.change_vs_last_month_pct}% vs last month)` : ""}`} />
            <Tile label="Savings on the table" value={`${money(e.savings.open_monthly, cur)}/mo`} to="/savings"
              note={`${e.savings.open_count} item(s); ${money(e.savings.this_week_monthly, cur)}/mo doable this week`} />
            <Tile label="Savings kept" value={`${money(e.savings.kept_latest_month, cur)}/mo`} to="/savings"
              note={e.savings.kept_month ? `Verified on the bill in ${e.savings.kept_month}` : "Nothing verified yet"} />
            <Tile label="Budgets at risk" value={`${e.budgets.at_risk.length} of ${e.budgets.count}`} to="/governance?tab=budgets"
              note={e.budgets.at_risk.map((b) => `${b.name} ${b.forecast_pct ?? 0}%`).join(", ") || "All on track"} />
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Tile label="Commitment coverage" value={e.commitments.coverage_pct != null ? `${e.commitments.coverage_pct.toFixed(0)}%` : "–"}
              note={e.commitments.utilization_pct != null ? `${e.commitments.utilization_pct.toFixed(0)}% of what was bought is used` : undefined} to="/savings/commitments" />
            <Tile label={e.unit_cost ? `Cost per ${e.unit_cost.unit}` : "Unit cost"} to="/governance?tab=unit-costs"
              value={e.unit_cost?.per_unit != null ? `${e.unit_cost.per_unit.toFixed(e.unit_cost.per_unit < 1 ? 4 : 2)} ${cur}` : "–"}
              note={e.unit_cost ? `${e.unit_cost.name}${e.unit_cost.change_pct != null ? `, ${e.unit_cost.change_pct > 0 ? "+" : ""}${e.unit_cost.change_pct}% recently` : ""}` : "Add a unit metric"} />
            <Tile label="Policy violations" value={String(e.policy_violations)} to="/governance?tab=policies" />
            <Tile label="Cost anomalies this month" value={String(e.anomalies_this_month)} to="/costs" />
          </div>
          <Card>
            <CardHeader><CardTitle>Biggest changes (last 30 days)</CardTitle></CardHeader>
            <CardContent>
              <ul className="grid gap-1 text-sm" aria-label="Biggest changes">
                {e.movers.map((m) => (
                  <li key={m.key} className="flex justify-between gap-2">
                    <span className="truncate">{m.label ?? m.key}</span>
                    <span className={`tabular-nums ${m.change > 0 ? "text-destructive" : "text-primary"}`}>{m.change > 0 ? "+" : ""}{money(m.change, cur)}</span>
                  </li>
                ))}
                {e.movers.length === 0 && <li className="text-muted-foreground">No notable changes.</li>}
              </ul>
            </CardContent>
          </Card>
        </>
      )}
      <p className="text-xs text-muted-foreground">
        Words on this page: <Term id="forecast" />, <Term id="coverage" />, <Term id="utilization" />, <Term id="unit_cost" />,{" "}
        <Term id="anomaly" />. <Link to="/glossary" className="underline underline-offset-4">All words</Link>
      </p>
      {r && (
        <Card>
          <CardHeader>
            <CardTitle>{r.month_label} on one page</CardTitle>
            <CardDescription>
              The monthly leadership report{r.closed ? "" : " (month not closed yet, so figures may still change)"}.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <ul className="grid list-disc gap-1 pl-5 text-sm" aria-label="Leadership report">
              {r.sentences.map((s) => <li key={s}>{s}</li>)}
            </ul>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" asChild>
                <a href="/api/v1/reports/download?kind=leadership" download><Download /> Download PDF</a>
              </Button>
              <Button size="sm" variant="ghost" asChild><Link to="/governance?tab=reports">Email it every month</Link></Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
