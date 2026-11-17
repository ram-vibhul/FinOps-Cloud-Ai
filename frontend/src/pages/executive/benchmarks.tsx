import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, type BenchmarkKey, errorMessage } from "@/lib/api";
import { numberLocale } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const KEYS: BenchmarkKey[] = ["effective_savings_rate", "waste_pct", "allocated_pct", "vcpu_hour_cost"];

function show(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined) return "–";
  if (unit === "%") return `${value.toLocaleString(numberLocale(), { maximumFractionDigits: 1 })}%`;
  return `${value.toLocaleString(numberLocale(), { maximumFractionDigits: 4 })} ${unit}`;
}

/** Peer benchmarks (11.40): opt in to compare four ratios with other organisations. */
export function BenchmarksCard() {
  const me = useMe();
  const demo = Boolean(activeOrg(me)?.is_demo);
  const canChange = atLeast(me.role, "admin") && !demo;
  const load = useLoad(() => api.benchmarks(), [me.active_organization_id]);
  const [busy, setBusy] = useState(false);
  const data = load.data;
  if (!data) return null;

  async function toggle(on: boolean) {
    setBusy(true);
    try {
      await api.setBenchmarkOptIn(on);
      await load.reload();
      toast.success(on ? "Sharing your ratios with peers." : "Stopped sharing; your shared ratios were deleted.");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const peers = data.peers;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Compared with peers, {data.month.slice(0, 7)}</CardTitle>
        <CardDescription>
          Opt in to share four ratios (never amounts, names, accounts or tags) and see how other organisations
          compare. Peer figures appear once {data.min_peers} organisations share them
          {peers ? ` (now ${peers.organisations}, ${peers.scope === "size band" ? `your size band: ${data.size_band}` : "all sizes"})` : ""}.
          {data.sample && " The demo shows made-up peer figures."}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <Table aria-label="Benchmarks">
          <THead><TR><TH>Measure</TH><TH className="text-right">You</TH><TH className="text-right">Peers (middle half)</TH><TH className="text-right">Peer median</TH></TR></THead>
          <TBody>
            {KEYS.map((k) => {
              const def = data.definitions[k];
              const p = peers?.metrics[k];
              const ready = p?.median !== undefined;
              return (
                <TR key={k}>
                  <TD>{def.label}<span className="block text-xs text-muted-foreground">{def.better} is better</span></TD>
                  <TD className="text-right tabular-nums">{show(data.mine[k], def.unit)}</TD>
                  <TD className="text-right tabular-nums">{ready ? `${show(p?.p25, def.unit)} – ${show(p?.p75, def.unit)}` : "–"}</TD>
                  <TD className="text-right tabular-nums">{ready ? show(p?.median, def.unit) : "–"}</TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
        {canChange && (
          <div>
            {data.opted_in
              ? <Button variant="outline" disabled={busy} onClick={() => void toggle(false)}>Stop sharing</Button>
              : <Button disabled={busy} onClick={() => void toggle(true)}>Share my ratios and compare</Button>}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
