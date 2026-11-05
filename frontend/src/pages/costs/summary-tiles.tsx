import { TrendingDown, TrendingUp } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import type { CostSummary } from "@/lib/api";
import { money, percent, shortDate } from "@/lib/format";

function Change({ pct, label }: { pct: number | null; label: string }) {
  if (pct === null) return <span className="text-xs text-muted-foreground">{label}: no comparison yet</span>;
  const Icon = pct >= 0 ? TrendingUp : TrendingDown;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <Icon className="size-3.5" aria-hidden />
      {percent(pct)} {label}
    </span>
  );
}

function Tile({ title, value, children }: { title: string; value: string; children?: React.ReactNode }) {
  return (
    <Card className="gap-1 py-4">
      <CardContent className="flex flex-col gap-1 px-4">
        <span className="text-sm text-muted-foreground">{title}</span>
        <span className="text-2xl font-semibold tabular-nums tracking-tight">{value}</span>
        {children}
      </CardContent>
    </Card>
  );
}

export function SummaryTiles({ summary }: { summary: CostSummary }) {
  const c = summary.currency;
  const f = summary.forecast;
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Tile title="Month to date" value={money(summary.month_to_date, c)}>
        <Change pct={summary.change_vs_previous_month_pct} label="vs same days last month" />
      </Tile>
      <Tile title="Forecast for this month" value={money(f.amount, c)}>
        <span className="text-xs text-muted-foreground">
          likely {money(f.low, c)} – {money(f.high, c)}
        </span>
      </Tile>
      <Tile title="Last month" value={money(summary.last_month, c)}>
        <Change pct={f.change_vs_last_month_pct} label="forecast vs last month" />
      </Tile>
      <Tile title={summary.data_through ? `Latest day (${shortDate(summary.data_through)})` : "Latest day"}
        value={money(summary.latest_day, c)}>
        <span className="text-xs text-muted-foreground">typical day {money(f.daily_rate, c)}</span>
      </Tile>
    </div>
  );
}
