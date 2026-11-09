import type { EChartsCoreOption } from "echarts/core";
import { useCallback, useMemo } from "react";
import { type ChartColors, EChart } from "@/components/chart/echart";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import type { Correlation } from "@/lib/api";
import { money } from "@/lib/format";

function hourLabel(iso: string): string {
  const d = new Date(iso);
  return `${d.getDate()} ${d.toLocaleString(undefined, { month: "short" })} ${String(d.getHours()).padStart(2, "0")}:00`;
}

function nearestIndex(hours: string[], iso: string): number {
  const t = Date.parse(iso);
  let best = 0;
  hours.forEach((h, i) => {
    if (Math.abs(Date.parse(h) - t) < Math.abs(Date.parse(hours[best] ?? h) - t)) best = i;
  });
  return best;
}

function SmallChart({ title, values, labels, markers, format }: {
  title: string; values: (number | null)[]; labels: string[];
  markers: { index: number; label: string; type: string }[]; format: (v: number) => string;
}) {
  const build = useCallback((c: ChartColors): EChartsCoreOption => ({
    grid: { left: 8, right: 8, top: 28, bottom: 8, containLabel: true },
    title: undefined,
    tooltip: { trigger: "axis", valueFormatter: (v: unknown) => (v == null ? "–" : format(Number(v))),
      backgroundColor: c.surface, borderColor: c.grid, textStyle: { color: c.text } },
    xAxis: { type: "category", data: labels, axisTick: { show: false }, axisLine: { lineStyle: { color: c.grid } },
      axisLabel: { color: c.muted, fontSize: 10, hideOverlap: true } },
    yAxis: { type: "value", splitNumber: 2, axisLabel: { color: c.muted, fontSize: 10, formatter: (v: number) => format(v) },
      splitLine: { lineStyle: { color: c.grid } } },
    series: [{
      name: title, type: "line", data: values, connectNulls: false, showSymbol: false, smooth: false,
      lineStyle: { width: 2, color: c.series[0] }, itemStyle: { color: c.series[0] },
      markLine: {
        symbol: "none", silent: true,
        label: { show: false },
        data: markers.map((m) => ({
          xAxis: m.index,
          lineStyle: { color: m.type === "incident" ? c.series[3] ?? c.text : c.text, type: "dashed", width: 1 },
        })),
      },
    }],
  }), [values, labels, markers, title, format]);
  return (
    <figure className="grid gap-1">
      <figcaption className="text-xs font-medium text-muted-foreground">{title}</figcaption>
      <EChart build={build} height={130} label={`${title} per hour, with change and first-warning markers`} />
    </figure>
  );
}

export function CorrelationCard({ correlation }: { correlation: Correlation }) {
  const { series, currency } = correlation;
  const labels = useMemo(() => series.hours.map(hourLabel), [series.hours]);
  const markers = useMemo(
    () => correlation.markers.map((m) => ({ index: nearestIndex(series.hours, m.at), label: m.label, type: m.type })),
    [correlation.markers, series.hours],
  );
  const fmtMoney = useCallback((v: number) => money(v, currency), [currency]);
  const fmtCores = useCallback((v: number) => `${Number(v.toFixed(2))}`, []);
  const fmtGib = useCallback((v: number) => `${Number(v.toFixed(2))} GiB`, []);
  if (!correlation.anchor && correlation.effects.length === 0 && series.hours.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Cause and effect</CardTitle>
        <CardDescription>
          Deployments, resource use, cost and warnings for this workload on one timeline. Dashed lines mark changes; the
          coloured one marks the first warnings.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {correlation.story.length > 0 && (
          <ol className="grid gap-1 text-sm">
            {correlation.story.map((line) => (
              <li key={line} className={line.startsWith("→") ? "pl-4" : "font-medium"}>{line}</li>
            ))}
          </ol>
        )}
        {correlation.effects.some((e) => e.change_pct !== null) && (
          <Table>
            <THead><TR><TH>Signal</TH><TH className="text-right">Before the change</TH><TH className="text-right">After</TH><TH className="text-right">Change</TH></TR></THead>
            <TBody>
              {correlation.effects.filter((e) => e.change_pct !== null).map((e) => (
                <TR key={e.signal}>
                  <TD>{e.label}</TD>
                  <TD className="text-right tabular-nums">{e.signal === "cost" ? fmtMoney(e.before) : e.before.toFixed(2)}</TD>
                  <TD className="text-right tabular-nums">{e.signal === "cost" ? fmtMoney(e.after) : e.after.toFixed(2)}</TD>
                  <TD className="text-right tabular-nums">{(e.change_pct ?? 0) > 0 ? "+" : ""}{e.change_pct}%</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
        {series.hours.length > 1 && (
          <div className="grid gap-3 lg:grid-cols-3">
            <SmallChart title={`Allocated cost (${currency})`} values={series.cost} labels={labels} markers={markers} format={fmtMoney} />
            <SmallChart title="CPU per replica (cores)" values={series.cpu} labels={labels} markers={markers} format={fmtCores} />
            <SmallChart title="Memory per replica" values={series.memory} labels={labels} markers={markers} format={fmtGib} />
          </div>
        )}
        {correlation.cost_anomalies.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Cost anomalies in the same period: {correlation.cost_anomalies.map((a) => `${a.label} (${a.day}, +${money(a.impact, a.currency)})`).join("; ")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
