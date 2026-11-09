import { CheckCircle2, Copy, ListChecks, Search, Sparkles, Wrench } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { InvestigationResult } from "@/lib/api";
import { CorrelationCard } from "./correlation";

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function InvestigationView({ result }: { result: InvestigationResult }) {
  const f = result.facts;
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle>{result.target}</CardTitle>
          <CardDescription className="text-base text-foreground">{result.summary}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted-foreground">
          <span>{f.running} of {f.pods} pods running</span>
          <span>{f.restarts} restarts</span>
          <span>{f.warning_events} warning events</span>
          {f.nodes.length > 0 && <span>Nodes: {f.nodes.join(", ")}</span>}
          <span className="basis-full text-xs">Logs: {f.logs}</span>
        </CardContent>
      </Card>
      {(result.narrative || result.status_update) && (
        <div className="grid gap-4 lg:grid-cols-2">
          {result.narrative && (
            <Card>
              <CardHeader><CardTitle>In plain English</CardTitle></CardHeader>
              <CardContent className="grid gap-2 text-sm">
                <p>{result.narrative}</p>
                {result.worded_by === "ai" && (
                  <p className="flex items-center gap-1 text-xs text-muted-foreground">
                    <Sparkles className="size-3" aria-hidden /> Worded by AI from the findings below (secrets and personal data removed first).
                  </p>
                )}
              </CardContent>
            </Card>
          )}
          {result.status_update && (
            <Card>
              <CardHeader className="flex flex-row items-center justify-between gap-2">
                <CardTitle>Team status update</CardTitle>
                <Button size="sm" variant="secondary"
                  onClick={() => void navigator.clipboard?.writeText(result.status_update ?? "").then(() => toast.success("Copied."))}>
                  <Copy /> Copy
                </Button>
              </CardHeader>
              <CardContent>
                <pre className="whitespace-pre-wrap rounded bg-muted p-3 font-sans text-sm">{result.status_update}</pre>
              </CardContent>
            </Card>
          )}
        </div>
      )}
      {result.correlation && <CorrelationCard correlation={result.correlation} />}
      <ol className="grid gap-4">
        {result.causes.map((c, i) => (
          <li key={c.check}>
            <Card className={i === 0 ? "border-primary/50" : undefined}>
              <CardHeader>
                <CardTitle className="flex flex-wrap items-center gap-2">
                  <span className="text-muted-foreground">{i + 1}.</span> {c.title}
                  <Badge variant={c.confidence >= 0.75 ? "default" : "secondary"}>{Math.round(c.confidence * 100)}% likely</Badge>
                </CardTitle>
                <CardDescription>{c.summary}</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4 text-sm md:grid-cols-3">
                <section>
                  <h3 className="mb-1 flex items-center gap-1 font-medium"><Search className="size-4" aria-hidden /> Evidence</h3>
                  <ul className="list-disc space-y-1 pl-5">{c.evidence.map((e) => <li key={e}>{e}</li>)}</ul>
                </section>
                <section>
                  <h3 className="mb-1 flex items-center gap-1 font-medium"><Wrench className="size-4" aria-hidden /> What to do</h3>
                  <ul className="list-disc space-y-1 pl-5">{c.fixes.map((e) => <li key={e}>{e}</li>)}</ul>
                </section>
                <section>
                  <h3 className="mb-1 flex items-center gap-1 font-medium"><ListChecks className="size-4" aria-hidden /> How to know it worked</h3>
                  <ul className="list-disc space-y-1 pl-5">{c.verify.map((e) => <li key={e}>{e}</li>)}</ul>
                </section>
              </CardContent>
            </Card>
          </li>
        ))}
      </ol>
      {result.causes.length === 0 && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <CheckCircle2 className="size-4" aria-hidden /> Nothing looks wrong in this window.
        </p>
      )}
      {result.timeline.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Timeline</CardTitle></CardHeader>
          <CardContent>
            <ol className="grid gap-2 text-sm">
              {result.timeline.toSorted((a, b) => b.at.localeCompare(a.at)).map((t) => (
                <li key={`${t.at}-${t.reason}-${t.object}`} className="grid grid-cols-[8rem_1fr] gap-2">
                  <span className="tabular-nums text-muted-foreground">{when(t.at)}</span>
                  <span>
                    <Badge variant={t.type === "Warning" ? "warning" : t.type === "Change" ? "default" : "secondary"}>{t.reason}</Badge>{" "}
                    <span className="text-muted-foreground">{t.object}</span> {t.message}
                    {t.count > 1 && <span className="text-muted-foreground"> (x{t.count})</span>}
                  </span>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
