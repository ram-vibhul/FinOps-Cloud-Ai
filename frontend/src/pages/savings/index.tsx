import { ChevronDown, ChevronRight, ClipboardCheck, Copy, PiggyBank, RefreshCw } from "lucide-react";
import { Link } from "react-router";
import { useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type Finding } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { CommitmentsCard } from "./commitments";
import { FixesCard } from "./fixes";
import { KeptCard } from "./kept";
import { QuickWinsCard } from "./plan";
import { RulesCard } from "./rules";
import { WorkflowPanel } from "./workflow";
import type { Member } from "@/lib/api";

const CATEGORY: Record<string, string> = {
  waste: "Waste", rightsizing: "Right-sizing", commitments: "Commitments", network: "Network",
  kubernetes: "Kubernetes", database: "Databases", storage: "Storage", schedule: "Schedules",
  containers: "Containers", observability: "Observability", imported: "From your cloud",
  ai: "AI usage",
};
const LEVEL = { low: "secondary", medium: "warning", high: "destructive" } as const;

function copy(text: string) {
  void navigator.clipboard?.writeText(text).then(() => toast.success("Copied."));
}

const STATUS_LABEL: Record<Finding["status"], string> = {
  open: "Open", approved: "Approved", applied: "Applied", verified: "Verified", dismissed: "Dismissed", gone: "Gone",
};

interface AdviceSource { source: string; label: string; title: string; savings_monthly: number | null }

interface PipelineInfo { step?: string; superseded_by?: string; sized_on?: number; order?: string[]; aws_hourly?: number; sized_hourly?: number }

function confidenceOf(f: Finding): { level: string; reasons: string[] } | null {
  return ((f.evidence as { confidence?: { level: string; reasons: string[] } }).confidence) ?? null;
}

function pipelineOf(f: Finding): PipelineInfo {
  return ((f.evidence as { pipeline?: PipelineInfo }).pipeline) ?? {};
}

function adviceSources(f: Finding): { imported: boolean; sources: AdviceSource[] } {
  const ev = f.evidence as { imported?: boolean; sources?: AdviceSource[]; also_recommended_by?: AdviceSource[] };
  return { imported: Boolean(ev.imported), sources: (ev.imported ? ev.sources : ev.also_recommended_by) ?? [] };
}

function FindingRow({ f, canAct, isAdmin, members, onChanged }: {
  f: Finding; canAct: boolean; isAdmin: boolean; members: Member[]; onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const advice = adviceSources(f);
  const pipe = pipelineOf(f);

  return (
    <li className="grid gap-2 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <button type="button" className="flex min-w-0 items-start gap-2 text-left" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? <ChevronDown className="mt-0.5 size-4 shrink-0" aria-hidden /> : <ChevronRight className="mt-0.5 size-4 shrink-0" aria-hidden />}
          <span className="min-w-0">
            <span className="font-medium">{f.title}</span>
            <span className="mt-1 flex flex-wrap gap-1 text-xs">
              <Badge variant="outline">{CATEGORY[f.category] ?? f.category}</Badge>
              <Badge variant={LEVEL[f.risk]}>{f.risk} risk</Badge>
              <Badge variant={LEVEL[f.effort]}>{f.effort} effort</Badge>
              {f.confidence && f.confidence !== "high" && (
                <Badge variant={f.confidence === "low" ? "destructive" : "outline"}>{f.confidence} confidence</Badge>
              )}
              {f.overlaps && <Badge variant="secondary">overlaps a bigger saving</Badge>}
              {f.team && <Badge variant="outline">team: {f.team}</Badge>}
              {advice.imported && <Badge variant="secondary">from AWS</Badge>}
              {pipe.superseded_by && <Badge variant="secondary">replaced by a better option</Badge>}
              {!advice.imported && advice.sources.length > 0 && <Badge variant="default">AWS agrees</Badge>}
              {f.status !== "open" && <Badge variant={f.status === "verified" ? "default" : "secondary"}>{STATUS_LABEL[f.status]}</Badge>}
            </span>
          </span>
        </button>
        <span className="text-right">
          <span className="block text-lg font-semibold tabular-nums">{money(f.savings_monthly, f.currency)}</span>
          <span className="text-xs text-muted-foreground">per month</span>
        </span>
      </div>
      {open && (
        <div className="grid gap-3 pl-6 text-sm">
          {f.description && <p>{f.description}</p>}
          {confidenceOf(f) && (
            <p className="text-muted-foreground">
              <span className="font-medium text-foreground">{confidenceOf(f)!.level[0]!.toUpperCase() + confidenceOf(f)!.level.slice(1)} confidence:</span>{" "}
              {confidenceOf(f)!.reasons.join("; ")}.
            </p>
          )}
          {pipe.superseded_by && <p className="text-muted-foreground">Replaced by: {pipe.superseded_by}</p>}
          {!pipe.superseded_by && (pipe.order?.length ?? 0) > 1 && (
            <p className="text-muted-foreground">
              Do these in order; each saving is sized on what the step before leaves: {pipe.order!.join(" → ")}.
            </p>
          )}
          {pipe.step === "commitments" && pipe.aws_hourly != null && (
            <p className="text-muted-foreground">
              Sized after the other suggestions: AWS recommends {pipe.aws_hourly.toFixed(2)}/hour based on today's
              usage; once right-sizing and clean-up are done, {pipe.sized_hourly?.toFixed(2)}/hour covers what is left.
            </p>
          )}
          {advice.sources.length > 0 && (
            <ul className="grid gap-1 rounded-md bg-muted/50 p-2 text-xs" aria-label="AWS recommendations">
              {advice.sources.map((s) => (
                <li key={`${s.source}${s.title}`}>
                  <span className="font-medium">{advice.imported ? "Source" : "Also recommended by"}: {s.label}</span>
                  {" — "}{s.title}
                  {s.savings_monthly != null && <> ({money(s.savings_monthly, f.currency)} a month by its estimate)</>}
                </li>
              ))}
            </ul>
          )}
          {f.steps.length > 0 && (
            <ol className="list-decimal space-y-1 pl-5">{f.steps.map((s) => <li key={s}>{s}</li>)}</ol>
          )}
          {f.commands.map((c) => (
            <div key={c} className="flex items-start gap-2">
              <pre className="flex-1 overflow-x-auto rounded bg-muted p-2 text-xs"><code>{c}</code></pre>
              <Button size="icon" variant="ghost" aria-label="Copy command" onClick={() => copy(c)}><Copy /></Button>
            </div>
          ))}
          <p className="text-xs text-muted-foreground">
            {[f.service, f.account_id, f.region, f.resource_id].filter(Boolean).join(" · ")} · found{" "}
            {new Date(f.first_seen_at).toLocaleDateString()}
            {f.status_note ? ` · note: ${f.status_note}` : ""}
          </p>
          {canAct && <WorkflowPanel f={f} isAdmin={isAdmin} members={members} onChanged={onChanged} />}
        </div>
      )}
    </li>
  );
}

export function SavingsPage() {
  const me = useMe();
  const canAct = atLeast(me.role, "member") && !activeOrg(me)?.is_demo;
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState<string>("active");
  const [owner, setOwner] = useState("");
  const isAdmin = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const members = useLoad(() => (canAct ? api.members() : Promise.resolve([] as Member[])), [me.active_organization_id, canAct]);
  const data = useLoad(() => api.findings({ status, category, owner }), [me.active_organization_id, status, category, owner]);
  const [running, setRunning] = useState(false);
  const d = data.data;
  const currency = d?.items[0]?.currency ?? "USD";

  async function runNow() {
    setRunning(true);
    try {
      await api.runFindings();
      await data.reload();
      toast.success("Checked again.");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Savings</h1>
          <p className="text-sm text-muted-foreground">Ranked by monthly saving, adjusted for risk and effort.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <NativeSelect aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {Object.entries(CATEGORY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </NativeSelect>
          <NativeSelect aria-label="Owner filter" value={owner} onChange={(e) => setOwner(e.target.value)}>
            <option value="">Anyone's</option>
            <option value="me">Mine</option>
            <option value="-">No owner</option>
          </NativeSelect>
          <NativeSelect aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="active">Being worked on</option>
            <option value="open">Open</option>
            <option value="approved">Approved</option>
            <option value="applied">Applied</option>
            <option value="verified">Verified</option>
            <option value="dismissed">Dismissed</option>
          </NativeSelect>
          <Button variant="outline" asChild><Link to="/savings/audit"><ClipboardCheck /> AWS audit</Link></Button>
          <Button variant="outline" asChild><Link to="/savings/upgrades">Upgrades</Link></Button>
          <Button variant="outline" asChild><Link to="/savings/roi">ROI pack</Link></Button>
          {canAct && <Button variant="outline" disabled={running} onClick={() => void runNow()}><RefreshCw /> Check now</Button>}
        </div>
      </div>
      {d && (
        <div className="grid gap-4 sm:grid-cols-3">
          <Card><CardHeader><CardDescription>Possible saving</CardDescription>
            <CardTitle className="text-2xl tabular-nums">{money(d.summary.savings_monthly, currency)}<span className="text-base font-normal text-muted-foreground"> / month</span></CardTitle></CardHeader>
            {d.summary.overlapping > 0 && <CardContent className="text-xs text-muted-foreground">{d.summary.overlapping} overlapping finding(s) counted once.</CardContent>}
          </Card>
          <Card><CardHeader><CardDescription>Realised (verified in the bill)</CardDescription>
            <CardTitle className="text-2xl tabular-nums">{money(d.summary.realised_monthly ?? 0, currency)}<span className="text-base font-normal text-muted-foreground"> / month</span></CardTitle></CardHeader>
            <CardContent className="text-xs text-muted-foreground">{d.summary.count} finding(s) shown · {d.summary.verified_count ?? 0} verified</CardContent>
          </Card>
          <Card><CardHeader><CardDescription>By category</CardDescription></CardHeader>
            <CardContent className="grid gap-1 text-sm">
              {Object.entries(d.summary.by_category).map(([k, v]) => (
                <span key={k} className="flex justify-between"><span>{CATEGORY[k] ?? k} ({v.count})</span><span className="tabular-nums">{money(v.savings_monthly, currency)}</span></span>
              ))}
            </CardContent>
          </Card>
        </div>
      )}
      <QuickWinsCard reloadKey={data.data} />
      <FixesCard />
      <KeptCard />
      <CommitmentsCard canRefresh={atLeast(me.role, "admin") && !activeOrg(me)?.is_demo} />
      {d && d.items.length === 0 ? (
        <EmptyState icon={PiggyBank} title={status === "active" || status === "open" ? "No savings found right now" : "Nothing here"}>
          {status === "active" || status === "open" ? "Detectors run every day on your connected accounts and clusters." : "Findings with this status appear here."}
        </EmptyState>
      ) : d ? (
        <Card>
          <CardContent>
            <ul className="divide-y">
              {d.items.map((f) => (
                <FindingRow key={f.id} f={f} canAct={canAct} isAdmin={isAdmin} members={members.data ?? []} onChanged={() => void data.reload()} />
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}
      <RulesCard canEdit={canAct} onChanged={() => void data.reload()} />
    </div>
  );
}
