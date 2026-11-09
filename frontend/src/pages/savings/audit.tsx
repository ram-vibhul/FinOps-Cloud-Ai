import { CheckCircle2, ClipboardCheck, Eye, HelpCircle, PiggyBank, Play, Printer } from "lucide-react";
import { type ComponentType, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/native-select";
import { api, type AuditResult, type AuditStatus, errorMessage } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

export const STATUS: Record<AuditStatus, { label: string; icon: ComponentType<{ className?: string }>; variant: "default" | "warning" | "secondary" | "outline" }> = {
  saving: { label: "Saving found", icon: PiggyBank, variant: "default" },
  more_data: { label: "Needs more data", icon: HelpCircle, variant: "warning" },
  manual: { label: "Manual review", icon: Eye, variant: "secondary" },
  fine: { label: "Checked & fine", icon: CheckCircle2, variant: "outline" },
};
const ORDER: AuditStatus[] = ["saving", "more_data", "manual", "fine"];

export function StatusBadge({ status }: { status: AuditStatus }) {
  const s = STATUS[status];
  const Icon = s.icon;
  return (
    <Badge variant={s.variant} className="gap-1 whitespace-nowrap">
      <Icon className="size-3" aria-hidden /> {s.label}
    </Badge>
  );
}

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function ResultRow({ r, currency }: { r: AuditResult; currency: string }) {
  const prev = r.details.previous_status;
  return (
    <tr className="border-b align-top last:border-0 print:break-inside-avoid">
      <td className="py-2 pr-3">
        <p className="font-medium">{r.title}</p>
        <p className="text-xs text-muted-foreground">{r.service}</p>
      </td>
      <td className="py-2 pr-3">
        <StatusBadge status={r.status} />
        {prev && <p className="mt-1 text-xs text-muted-foreground">was: {STATUS[prev].label}</p>}
      </td>
      <td className="py-2 pr-3 text-sm">{r.reason}</td>
      <td className="py-2 pr-3 text-right text-sm tabular-nums">{r.spend_monthly ? money(r.spend_monthly, currency) : "—"}</td>
      <td className="py-2 text-right text-sm font-medium tabular-nums">{r.savings_monthly ? money(r.savings_monthly, currency) : "—"}</td>
    </tr>
  );
}

export function AuditPage() {
  const me = useMe();
  const canRun = atLeast(me.role, "member") && !activeOrg(me)?.is_demo;
  const [params, setParams] = useSearchParams();
  const runs = useLoad(() => api.audits(), [me.active_organization_id]);
  const selected = params.get("run") ?? runs.data?.[0]?.id ?? null;
  const detail = useLoad(() => (selected ? api.audit(selected) : Promise.resolve(null)), [selected]);
  const [status, setStatus] = useState<"" | AuditStatus>("");
  const [area, setArea] = useState("");
  const [running, setRunning] = useState(false);

  async function runNow() {
    setRunning(true);
    try {
      const run = await api.runAudit();
      toast.success("Audit finished.");
      await runs.reload();
      setParams({ run: run.id }, { replace: true });
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setRunning(false);
    }
  }

  const run = detail.data?.run;
  const currency = run?.currency ?? "USD";
  const areas = run?.summary.areas ?? {};
  const results = (detail.data?.results ?? []).filter((r) => (!status || r.status === status) && (!area || r.area === area));
  const grouped = Object.keys(areas)
    .map((key) => ({ key, label: areas[key] ?? key, rows: results.filter((r) => r.area === key) }))
    .filter((g) => g.rows.length > 0);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-muted-foreground print:hidden"><Link to="/savings" className="underline underline-offset-4">Savings</Link> /</p>
          <h1 className="text-2xl font-semibold tracking-tight">AWS audit</h1>
          <p className="text-sm text-muted-foreground">
            Every AWS service, checked one by one. Each row ends as a saving, checked & fine, needing more data, or for manual review.
          </p>
          {run && (
            <p className="mt-1 text-sm text-muted-foreground">
              {activeOrg(me)?.name} · {when(run.started_at)} · {run.trigger === "scheduled" ? "weekly run" : `run by ${run.requested_by ?? "someone"}`}
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2 print:hidden">
          {(runs.data?.length ?? 0) > 1 && (
            <NativeSelect aria-label="Audit run" value={selected ?? ""} onChange={(e) => setParams({ run: e.target.value }, { replace: true })}>
              {runs.data!.map((r) => <option key={r.id} value={r.id}>{when(r.started_at)}</option>)}
            </NativeSelect>
          )}
          {run && <Button variant="outline" onClick={() => window.print()}><Printer /> Print report</Button>}
          {canRun && <Button disabled={running} onClick={() => void runNow()}><Play /> {running ? "Running…" : "Run audit"}</Button>}
        </div>
      </div>

      {!runs.loading && (runs.data ?? []).length === 0 ? (
        <EmptyState icon={ClipboardCheck} title="No audit yet">
          Run the audit to check every AWS service you use. It also runs every Monday.
        </EmptyState>
      ) : run ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <Card className="h-full">
              <CardHeader><CardTitle className="text-sm font-medium text-muted-foreground">Savings found</CardTitle></CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold tabular-nums">{money(run.summary.savings_monthly, currency)}</p>
                <p className="text-xs text-muted-foreground">a month, counted once per resource</p>
              </CardContent>
            </Card>
            {ORDER.map((s) => (
              <button key={s} type="button" onClick={() => setStatus(status === s ? "" : s)} aria-pressed={status === s}
                aria-label={`${STATUS[s].label}: ${run.summary.counts[s]} of ${run.summary.checks} checks`}
                className="h-full rounded-xl text-left focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none">
                <Card className={status === s ? "h-full border-primary" : "h-full"}>
                  <CardHeader><CardTitle className="text-sm font-medium text-muted-foreground">{STATUS[s].label}</CardTitle></CardHeader>
                  <CardContent>
                    <p className="text-2xl font-semibold tabular-nums">{run.summary.counts[s]}</p>
                    <p className="text-xs text-muted-foreground">of {run.summary.checks} checks</p>
                  </CardContent>
                </Card>
              </button>
            ))}
          </div>
          {run.summary.changed > 0 && (
            <p className="text-sm text-muted-foreground">{run.summary.changed} check(s) changed since the previous audit.</p>
          )}
          <div className="flex flex-wrap gap-2 print:hidden">
            <NativeSelect aria-label="Area" value={area} onChange={(e) => setArea(e.target.value)}>
              <option value="">All areas</option>
              {Object.entries(areas).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </NativeSelect>
            <NativeSelect aria-label="Result" value={status} onChange={(e) => setStatus(e.target.value as "" | AuditStatus)}>
              <option value="">All results</option>
              {ORDER.map((s) => <option key={s} value={s}>{STATUS[s].label}</option>)}
            </NativeSelect>
          </div>
          {grouped.length === 0 && <p className="text-sm text-muted-foreground">No checks match these filters.</p>}
          {grouped.map((g) => (
            <Card key={g.key} className="print:break-inside-auto print:shadow-none">
              <CardHeader><CardTitle>{g.label}</CardTitle></CardHeader>
              <CardContent className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-left text-sm">
                  <thead className="text-xs text-muted-foreground">
                    <tr className="border-b">
                      <th scope="col" className="py-2 pr-3 font-medium">Check</th>
                      <th scope="col" className="py-2 pr-3 font-medium">Result</th>
                      <th scope="col" className="py-2 pr-3 font-medium">Why</th>
                      <th scope="col" className="py-2 pr-3 text-right font-medium">Spend / month</th>
                      <th scope="col" className="py-2 text-right font-medium">Saving / month</th>
                    </tr>
                  </thead>
                  <tbody>{g.rows.map((r) => <ResultRow key={r.check_id} r={r} currency={currency} />)}</tbody>
                </table>
              </CardContent>
            </Card>
          ))}
        </>
      ) : null}
    </div>
  );
}
