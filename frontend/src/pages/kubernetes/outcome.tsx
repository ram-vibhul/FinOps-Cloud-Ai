import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, type SavedInvestigation } from "@/lib/api";
import { useLoad } from "@/lib/use-load";

export function CloseInvestigationCard({ inv, canClose, onClosed }: {
  inv: SavedInvestigation; canClose: boolean; onClosed: () => void;
}) {
  const acc = useLoad(() => api.diagnosisAccuracy(), []);
  const causes = acc.data?.causes ?? [];
  const label = (id?: string | null) => causes.find((c) => c.check === id)?.label ?? id ?? "–";
  const [cause, setCause] = useState(inv.result?.causes[0]?.check ?? "other");
  const [why, setWhy] = useState("");
  const [fix, setFix] = useState("");

  async function close(event: FormEvent) {
    event.preventDefault();
    try {
      await api.closeInvestigation(inv.id, { real_cause: cause, cause_note: why.trim() || undefined, fix_note: fix.trim() || undefined });
      toast.success("Closed. Thanks — this improves future diagnoses.");
      onClosed();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  if (inv.real_cause) {
    const right = inv.cause_rank === 1;
    return (
      <Card>
        <CardHeader><CardTitle>Outcome</CardTitle></CardHeader>
        <CardContent className="grid gap-1 text-sm">
          <p className="flex flex-wrap items-center gap-2">
            Real cause: <span className="font-medium">{label(inv.real_cause)}</span>
            <Badge variant={right ? "default" : "secondary"}>
              {right ? "CloudCostRadar ranked it first" : inv.cause_rank ? `CloudCostRadar ranked it #${inv.cause_rank}` : "CloudCostRadar missed it"}
            </Badge>
          </p>
          {inv.cause_note && <p>Why: {inv.cause_note}</p>}
          {inv.fix_note && <p>Fixed by: {inv.fix_note}</p>}
        </CardContent>
      </Card>
    );
  }
  if (!canClose) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Close with the real cause</CardTitle>
        <CardDescription>Tell CloudCostRadar what it really was and what fixed it. This measures and improves its diagnoses.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={(e) => void close(e)} className="grid gap-3 sm:grid-cols-3 sm:items-end">
          <div className="grid gap-1">
            <Label htmlFor="real-cause">Real cause</Label>
            <NativeSelect id="real-cause" value={cause} onChange={(e) => setCause(e.target.value)}>
              {causes.map((c) => <option key={c.check} value={c.check}>{c.label}</option>)}
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="cause-note">What caused it (optional)</Label>
            <Input id="cause-note" value={why} onChange={(e) => setWhy(e.target.value)} maxLength={1000} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="fix-note">What fixed it (optional)</Label>
            <Input id="fix-note" value={fix} onChange={(e) => setFix(e.target.value)} maxLength={1000} />
          </div>
          <Button type="submit" className="justify-self-start">Close investigation</Button>
        </form>
      </CardContent>
    </Card>
  );
}

export function AccuracyCard() {
  const acc = useLoad(() => api.diagnosisAccuracy(), []);
  const a = acc.data;
  if (!a) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Diagnosis accuracy</CardTitle>
        <CardDescription>
          How often the top-ranked cause was the real one, from investigations closed with their real cause.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><dt className="text-muted-foreground">Closed investigations</dt><dd className="text-xl font-semibold tabular-nums">{a.closed}</dd></div>
          <div><dt className="text-muted-foreground">Top cause right</dt><dd className="text-xl font-semibold tabular-nums">{a.top1_pct == null ? "–" : `${a.top1_pct}%`}</dd></div>
          <div><dt className="text-muted-foreground">In the top 3</dt><dd className="text-xl font-semibold tabular-nums">{a.top3_pct == null ? "–" : `${a.top3_pct}%`}</dd></div>
          <div><dt className="text-muted-foreground">Lab score (recorded failures)</dt>
            <dd className="text-xl font-semibold tabular-nums">{a.lab ? `${a.lab.correct}/${a.lab.scenarios}` : "–"}</dd></div>
        </dl>
        {a.checks.length > 0 && (
          <Table>
            <THead><TR><TH>Cause</TH><TH className="text-right">Real cause</TH><TH className="text-right">Found first</TH>
              <TH className="text-right">Ranked first</TH><TH className="text-right">Right when first</TH></TR></THead>
            <TBody>
              {a.checks.map((c) => (
                <TR key={c.check}>
                  <TD>{c.label}</TD>
                  <TD className="text-right tabular-nums">{c.real}</TD>
                  <TD className="text-right tabular-nums">{c.recall_pct == null ? "–" : `${c.recall_pct}%`}</TD>
                  <TD className="text-right tabular-nums">{c.ranked_first}</TD>
                  <TD className="text-right tabular-nums">{c.precision_pct == null ? "–" : `${c.precision_pct}%`}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
        {a.closed === 0 && <p className="text-sm text-muted-foreground">Close investigations with their real cause to see accuracy here.</p>}
      </CardContent>
    </Card>
  );
}
