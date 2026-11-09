import { CheckCircle2, Circle, Loader2, PiggyBank } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type FirstSavings, type SetupChecklist } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

function Steps({ data, compact = false }: { data: SetupChecklist; compact?: boolean }) {
  return (
    <ol className="grid gap-2" aria-label="Setup steps">
      {data.steps.map((s) => {
        const isNext = s.key === data.next;
        return (
          <li key={s.key} className={`flex items-start gap-3 rounded-md p-2 ${isNext ? "border border-primary/40 bg-primary/5" : ""}`}>
            {s.done ? <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-primary" aria-label="Done" /> : <Circle className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-label="To do" />}
            <span className="flex-1">
              <span className={s.done ? "text-muted-foreground line-through" : "font-medium"}>{s.title}</span>
              {!compact && <span className="block text-sm text-muted-foreground">{s.why}</span>}
            </span>
            {!s.done && <Button asChild size="sm" variant={isNext ? "default" : "outline"}><Link to={s.link}>{isNext ? "Do this next" : "Open"}</Link></Button>}
          </li>
        );
      })}
    </ol>
  );
}

const WORKING: FirstSavings["stage"][] = ["importing", "analysing"];
const POLL_MS = 10_000;
const STAGE_TEXT: Record<FirstSavings["stage"], string> = {
  connect: "Connect a cloud account and CostLens looks for savings straight away.",
  importing: "Importing your costs. This usually takes a few minutes.",
  analysing: "Costs are in. Looking for savings now…",
  ready: "",
  none_found: "No savings found yet. CostLens checks again every day as more data arrives.",
};

/** The first savings of a new organisation, live while the first import runs (9.5). */
export function FirstSavingsPanel() {
  const me = useMe();
  const { data, reload } = useLoad(() => api.firstSavings(), [me.active_organization_id]);
  const working = data ? WORKING.includes(data.stage) : false;
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => void reload(), POLL_MS);
    return () => clearInterval(timer);
  }, [working, reload]);
  if (!data || data.stage === "connect") return null;
  return (
    <section aria-label="First savings" className="rounded-md border bg-primary/5 p-3">
      {data.stage === "ready" ? (
        <>
          <p className="flex items-center gap-2 font-medium">
            <PiggyBank className="size-5 text-primary" aria-hidden />
            First savings: about {money(data.savings_monthly, data.currency)} a month
          </p>
          <ul className="mt-2 grid gap-1">
            {data.top.map((t) => (
              <li key={t.id} className="flex justify-between gap-3">
                <span className="truncate">{t.title}</span>
                <span className="shrink-0 tabular-nums">{money(t.savings_monthly, t.currency)}</span>
              </li>
            ))}
          </ul>
          <Button asChild size="sm" className="mt-2">
            <Link to="/savings">See all {data.count} savings</Link>
          </Button>
        </>
      ) : (
        <output className="flex items-center gap-2">
          {working && <Loader2 className="size-4 animate-spin" aria-hidden />}
          {STAGE_TEXT[data.stage]}
        </output>
      )}
    </section>
  );
}

/** The checklist on the Overview until every required step is done (or an admin hides it). */
export function SetupCard() {
  const me = useMe();
  const data = useLoad(() => api.setup(), [me.active_organization_id]);
  const d = data.data;
  if (!d || d.hidden || d.done === d.total || activeOrg(me)?.is_demo) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Getting started: {d.done} of {d.total} done</CardTitle>
        <CardDescription><Link to="/setup" className="underline underline-offset-4">Open the guided setup</Link></CardDescription>
      </CardHeader>
      <CardContent className="grid gap-2 text-sm">
        <meter className="w-full" min={0} max={d.total} value={d.done} aria-label="Setup progress" />
        <FirstSavingsPanel />
        <Steps data={d} compact />
        {atLeast(me.role, "admin") && (
          <Button size="sm" variant="ghost" className="justify-self-start" onClick={() => void api.hideSetup().then(() => data.reload())}>Hide this list</Button>
        )}
      </CardContent>
    </Card>
  );
}

/** The guided setup: one step at a time, with why each matters. */
export function SetupPage() {
  const me = useMe();
  const data = useLoad(() => api.setup(), [me.active_organization_id]);
  const d = data.data;
  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Set up CostLens</h1>
        <p className="text-sm text-muted-foreground">A few steps to your first savings. Each one ticks itself when it is done.</p>
      </div>
      {d && (
        <Card>
          <CardContent className="grid gap-3 pt-1">
            <meter className="w-full" min={0} max={d.total} value={d.done} aria-label="Setup progress" />
            <p className="text-sm">{d.done === d.total ? "All set. CostLens keeps looking for savings every day." : `${d.done} of ${d.total} done.`}</p>
            <FirstSavingsPanel />
            <Steps data={d} />
            <Button size="sm" variant="ghost" className="justify-self-start" onClick={() => void data.reload()}>Check again</Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
