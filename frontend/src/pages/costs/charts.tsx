import type { EChartsCoreOption } from "echarts/core";
import { useCallback, useMemo, useState } from "react";
import { type ChartColors, EChart } from "@/components/chart/echart";
import { Button } from "@/components/ui/button";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import type { CostSeries } from "@/lib/api";
import { money, monthLabel, shortDate } from "@/lib/format";

function seriesColor(colors: ChartColors, key: string, index: number): string {
  return key === "Other" ? colors.other : (colors.series[index % colors.series.length] ?? colors.other);
}

function axis(colors: ChartColors) {
  return {
    axisLine: { lineStyle: { color: colors.grid } },
    axisTick: { show: false },
    axisLabel: { color: colors.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: colors.grid } },
  };
}

/** Daily stacked bars (top groups + Other), with legend, tooltip and a table view. */
/** Drop leading periods with no spend at all (e.g. months before data starts). */
function trimLeading(data: CostSeries): CostSeries {
  const first = data.periods.findIndex((_, i) => data.series.some((s) => (s.values[i] ?? 0) !== 0));
  if (first <= 0) return data;
  return { ...data, periods: data.periods.slice(first), series: data.series.map((s) => ({ ...s, values: s.values.slice(first) })) };
}

export function StackedSpendChart({ data: raw, monthly = false, forecastRemainder }: {
  data: CostSeries; monthly?: boolean; forecastRemainder?: number;
}) {
  const [asTable, setAsTable] = useState(false);
  const data = useMemo(() => (monthly ? trimLeading(raw) : raw), [raw, monthly]);
  const c = data.currency;
  const labels = useMemo(
    () => data.periods.map((p) => (monthly ? monthLabel(p) : p.length > 10 ? p.slice(5) : shortDate(p))),
    [data, monthly],
  );
  const build = useCallback(
    (colors: ChartColors): EChartsCoreOption => {
      const bars = data.series.map((s, i) => ({
        name: s.label,
        type: "bar",
        stack: "spend",
        data: s.values,
        barMaxWidth: 28,
        itemStyle: { color: seriesColor(colors, s.key, i), borderColor: colors.surface, borderWidth: 1 },
        emphasis: { focus: "series" },
      }));
      if (forecastRemainder && forecastRemainder > 0) {
        bars.push({
          name: "Forecast (rest of month)",
          type: "bar",
          stack: "spend",
          data: data.periods.map((_, i) => (i === data.periods.length - 1 ? Math.round(forecastRemainder) : 0)),
          barMaxWidth: 28,
          itemStyle: { color: "transparent", borderColor: colors.muted, borderWidth: 1, borderType: "dashed" } as never,
          emphasis: { focus: "series" },
        });
      }
      return {
        grid: { left: 8, right: 8, top: 40, bottom: 8, containLabel: true },
        legend: { type: "scroll", top: 0, textStyle: { color: colors.text, fontSize: 12 }, itemWidth: 12, itemHeight: 12 },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          valueFormatter: (v: unknown) => money(Number(v), c),
          backgroundColor: colors.surface,
          borderColor: colors.grid,
          textStyle: { color: colors.text },
        },
        xAxis: { type: "category", data: labels, ...axis(colors), splitLine: { show: false } },
        yAxis: { type: "value", ...axis(colors), axisLabel: { color: colors.muted, formatter: (v: number) => money(v, c, { compact: true }) } },
        series: bars,
      };
    },
    [data, labels, c, forecastRemainder],
  );
  const title = monthly ? "Monthly spend" : "Daily spend";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={() => setAsTable(!asTable)} aria-pressed={asTable}>
          {asTable ? "Show chart" : "Show table"}
        </Button>
      </div>
      {asTable ? (
        <Table>
          <THead><TR><TH>Group</TH><TH className="text-right">Total</TH></TR></THead>
          <TBody>
            {data.series.map((s) => (
              <TR key={s.key}><TD>{s.label}</TD><TD className="text-right tabular-nums">{money(s.total, c)}</TD></TR>
            ))}
            <TR><TD className="font-medium">All</TD><TD className="text-right font-medium tabular-nums">{money(data.total, c)}</TD></TR>
          </TBody>
        </Table>
      ) : (
        <EChart build={build} label={`${title}, total ${money(data.total, c)}`} height={monthly ? 240 : 300} />
      )}
    </div>
  );
}
