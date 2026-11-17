import { Search } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, type Investigation } from "@/lib/api";
import { money } from "@/lib/format";

function signed(v: number, c: string): string {
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${money(Math.abs(v), c)}`;
}

/** Why did cost change (11.32)? Evidence first; AI only words the summary. */
export function InvestigateCard() {
  const [days, setDays] = useState(7);
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<Investigation | null>(null);
  async function run() {
    setBusy(true);
    try {
      setOut(await api.investigate(days));
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  const c = out?.currency ?? "USD";
  const drill = out ? [...(out.within.account ?? []).slice(0, 1), ...(out.within.region ?? []).slice(0, 1), ...(out.within.resource ?? []).slice(0, 3)] : [];
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-end justify-between gap-3">
        <div>
          <CardTitle>Why did cost change?</CardTitle>
          <CardDescription>Compares a period with the one before it and gathers the evidence: what grew, the changes made, anomalies, and fixes.</CardDescription>
        </div>
        <div className="flex items-end gap-2">
          <div className="grid gap-1">
            <Label htmlFor="inv-days">Period</Label>
            <NativeSelect id="inv-days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              <option value={7}>Last 7 days</option><option value={14}>Last 14 days</option><option value={30}>Last 30 days</option>
            </NativeSelect>
          </div>
          <Button onClick={() => void run()} disabled={busy}><Search /> Investigate</Button>
        </div>
      </CardHeader>
      {out && (
        <CardContent className="grid gap-4" aria-label="Investigation">
          <p>{out.summary}{out.worded_by_ai && <span className="text-xs text-muted-foreground"> (worded by AI from the facts below)</span>}</p>
          <Table aria-label="What grew">
            <THead><TR><TH>What grew</TH><TH className="text-right">Before</TH><TH className="text-right">Now</TH><TH className="text-right">Change</TH></TR></THead>
            <TBody>
              {[...out.drivers.filter((d) => d.change > 0).slice(0, 3), ...drill].map((d) => (
                <TR key={`${d.dimension}:${d.key}`}>
                  <TD><span className="text-xs text-muted-foreground">{d.dimension} </span>{d.label}</TD>
                  <TD className="text-right tabular-nums">{money(d.before, c)}</TD>
                  <TD className="text-right tabular-nums">{money(d.now, c)}</TD>
                  <TD className="text-right tabular-nums text-destructive">{signed(d.change, c)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
          {out.changes.length > 0 && (
            <div><h3 className="mb-1 text-sm font-medium">Changes around then</h3>
              <ul className="grid gap-1 text-sm">{out.changes.slice(0, 5).map((e) => (
                <li key={e.id}><Link to={`/radar?change=${e.id}`} className="underline underline-offset-4">{e.title}</Link> by {e.actor ?? "unknown"}
                  {e.monthly ? `, about ${money(e.monthly, c)} a month` : ""}</li>
              ))}</ul></div>
          )}
          {out.fixes.length > 0 && (
            <div><h3 className="mb-1 text-sm font-medium">Proposed fixes</h3>
              <ul className="grid gap-1 text-sm">{out.fixes.map((f) => (
                <li key={f.id}><Link to="/savings" className="underline underline-offset-4">{f.title}</Link>: saves {money(f.savings_monthly, c)} a month</li>
              ))}</ul></div>
          )}
        </CardContent>
      )}
    </Card>
  );
}
