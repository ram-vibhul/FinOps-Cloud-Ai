import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, type FixCheck, type InvestigationResult } from "@/lib/api";

const VERDICT = {
  fixed: { label: "Fixed", variant: "default" },
  improving: { label: "Improving", variant: "warning" },
  not_fixed: { label: "Not fixed", variant: "destructive" },
  too_early: { label: "Too early to tell", variant: "secondary" },
} as const;
const PERIOD = { before: "Before the incident", during: "During", after: "After the fix" } as const;

function num(v: number | null, digits = 2): string {
  return v == null ? "–" : v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function FixCheckCard({ clusterId, namespace, kind, name, result, investigationId, canSave }: {
  clusterId: string; namespace?: string | null; kind?: string | null; name?: string | null;
  result: InvestigationResult; investigationId?: string; canSave?: boolean;
}) {
  const changes = (result.correlation?.markers ?? []).filter((m) => m.type === "change");
  const [choice, setChoice] = useState(changes.length ? "change:0" : "time");
  const [time, setTime] = useState("");
  const [check, setCheck] = useState<FixCheck | null>(result.fix_check ?? null);
  if (!namespace || !kind || !name) return null;

  function fixParams(): { fix_at?: string } {
    if (choice.startsWith("change:")) {
      const m = changes[Number(choice.slice(7))];
      return { fix_at: m?.at };
    }
    return { fix_at: time ? new Date(time).toISOString() : undefined };
  }

  async function run(save: boolean) {
    try {
      const params = fixParams();
      if (!params.fix_at) {
        toast.error("Choose when the fix was made.");
        return;
      }
      setCheck(save && investigationId
        ? await api.saveFixCheck(investigationId, params)
        : await api.fixCheck({ cluster_id: clusterId, namespace: namespace ?? "", kind: kind ?? "", name: name ?? "", ...params }));
      if (save) toast.success("Saved on this investigation.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Did the fix work?</CardTitle>
        <CardDescription>Compares the workload before the incident, during it and after the fix.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="flex flex-wrap items-end gap-2">
          <div className="grid gap-1">
            <Label htmlFor="fix-choice">The fix</Label>
            <NativeSelect id="fix-choice" value={choice} onChange={(e) => setChoice(e.target.value)}>
              {changes.map((m, i) => <option key={m.at + m.label} value={`change:${i}`}>{m.label.slice(0, 80)}</option>)}
              <option value="time">At a time I choose…</option>
            </NativeSelect>
          </div>
          {choice === "time" && (
            <div className="grid gap-1">
              <Label htmlFor="fix-time">When</Label>
              <Input id="fix-time" type="datetime-local" value={time} onChange={(e) => setTime(e.target.value)} />
            </div>
          )}
          <Button variant="secondary" onClick={() => void run(false)}>Check</Button>
          {canSave && investigationId && <Button variant="outline" onClick={() => void run(true)}>Check and save</Button>}
        </div>
        {check && (
          <section aria-live="polite" className="grid gap-3">
            <p className="flex flex-wrap items-center gap-2 text-sm">
              <Badge variant={VERDICT[check.verdict].variant}>{VERDICT[check.verdict].label}</Badge> {check.text}
            </p>
            <Table>
              <THead>
                <TR><TH>Period</TH><TH className="text-right">Failures per hour</TH><TH className="text-right">CPU per replica</TH>
                  <TH className="text-right">Memory per replica (GiB)</TH><TH className="text-right">Cost per hour</TH></TR>
              </THead>
              <TBody>
                {check.periods.map((p) => (
                  <TR key={p.period}>
                    <TD>{PERIOD[p.period]}</TD>
                    <TD className="text-right tabular-nums">{num(p.failures_per_hour, 1)}</TD>
                    <TD className="text-right tabular-nums">{num(p.cpu)}</TD>
                    <TD className="text-right tabular-nums">{num(p.memory_gib)}</TD>
                    <TD className="text-right tabular-nums">{num(p.cost_per_hour, 4)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            {check.side_effects.length > 0 && (
              <ul className="list-disc pl-5 text-sm text-muted-foreground">
                {check.side_effects.map((s) => <li key={s}>Side effect: {s}</li>)}
              </ul>
            )}
          </section>
        )}
      </CardContent>
    </Card>
  );
}
