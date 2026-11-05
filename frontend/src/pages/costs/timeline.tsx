import type { EChartsCoreOption } from "echarts/core";
import { GitCommitHorizontal, Plus } from "lucide-react";
import { type FormEvent, useCallback, useState } from "react";
import { toast } from "sonner";
import { type ChartColors, EChart } from "@/components/chart/echart";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, type ChangeEvent, errorMessage, type Timeline } from "@/lib/api";
import { money, percent, shortDate } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

export function ChangeEffect({ change, currency }: { change: ChangeEvent; currency: string }) {
  const e = change.effect;
  if (!e) return <span className="text-xs text-muted-foreground">effect not known yet</span>;
  const sign = e.change_per_day > 0 ? "+" : "";
  return (
    <span className="text-xs text-muted-foreground">
      {e.scope}: {money(e.before_per_day, currency)}/day before, {money(e.after_per_day, currency)}/day after (
      {sign}{money(e.change_per_day, currency)}, {percent(e.change_pct)})
    </span>
  );
}

export function TimelineCard({ currency }: { currency: string }) {
  const me = useMe();
  const canAdd = atLeast(me.role, "member") && !activeOrg(me)?.is_demo;
  const data = useLoad<Timeline>(() => api.timeline(90), [me.active_organization_id]);
  const t = data.data;
  const build = useCallback(
    (colors: ChartColors): EChartsCoreOption => ({
      grid: { left: 8, right: 8, top: 16, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis", valueFormatter: (v: unknown) => money(Number(v), currency),
        backgroundColor: colors.surface, borderColor: colors.grid, textStyle: { color: colors.text } },
      xAxis: { type: "category", data: (t?.periods ?? []).map(shortDate), axisLine: { lineStyle: { color: colors.grid } },
        axisTick: { show: false }, axisLabel: { color: colors.muted, fontSize: 11 } },
      yAxis: { type: "value", splitLine: { lineStyle: { color: colors.grid } },
        axisLabel: { color: colors.muted, formatter: (v: number) => money(v, currency, { compact: true }) } },
      series: [{
        name: "Daily usage cost", type: "line", data: t?.values ?? [], showSymbol: false,
        lineStyle: { width: 2, color: colors.series[0] }, itemStyle: { color: colors.series[0] },
        markLine: {
          symbol: "none", silent: false,
          lineStyle: { color: colors.muted, type: "dashed", width: 1 },
          label: { show: false },
          data: (t?.changes ?? []).map((c) => ({ xAxis: shortDate(c.occurred_at.slice(0, 10)), name: c.title })),
        },
      }],
    }),
    [t, currency],
  );
  if (!t) return null;
  const recent = t.changes.toReversed().slice(0, 8);
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div>
          <CardTitle>What changed</CardTitle>
          <CardDescription>Daily usage cost with deployments and infrastructure changes (dashed lines).</CardDescription>
        </div>
        {canAdd && <RecordChangeDialog onDone={() => void data.reload()} />}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <EChart build={build} height={220} label={`Daily usage cost with ${t.changes.length} changes`} />
        {recent.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No changes recorded yet. Connect GitHub in Settings, or record deployments from your CI with the API.
          </p>
        ) : (
          <ul className="flex flex-col divide-y">
            {recent.map((c) => (
              <li key={c.id} className="flex items-start gap-3 py-2">
                <GitCommitHorizontal className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0">
                  <p className="text-sm">
                    <span className="font-medium">{c.title}</span>{" "}
                    <Badge variant="secondary">{c.kind}</Badge>{" "}
                    <span className="text-xs text-muted-foreground">{shortDate(c.occurred_at.slice(0, 10))} · {c.source}</span>
                  </p>
                  <ChangeEffect change={c} currency={currency} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function RecordChangeDialog({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<ChangeEvent["kind"]>("deployment");
  const [when, setWhen] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await api.createChange({ title: title.trim(), kind, ...(when ? { occurred_at: new Date(when).toISOString() } : {}) });
      toast.success("Change recorded.");
      setOpen(false);
      setTitle("");
      onDone();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm"><Plus /> Record a change</Button>
      </DialogTrigger>
      <DialogContent title="Record a change" description="Deployments, migrations, configuration changes, incidents.">
        <form onSubmit={(e) => void submit(e)} className="grid gap-3">
          <div className="grid gap-1">
            <Label htmlFor="change-title">What changed</Label>
            <Input id="change-title" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={300} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="change-kind">Type</Label>
            <NativeSelect id="change-kind" value={kind} onChange={(e) => setKind(e.target.value as ChangeEvent["kind"])}>
              <option value="deployment">Deployment</option>
              <option value="infrastructure">Infrastructure</option>
              <option value="config">Configuration</option>
              <option value="incident">Incident</option>
              <option value="other">Other</option>
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="change-when">When (leave empty for now)</Label>
            <Input id="change-when" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
          </div>
          <Button type="submit">Record</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
