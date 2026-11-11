import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { useLoad } from "@/lib/use-load";

export function QuickWinsCard({ reloadKey }: { reloadKey: unknown }) {
  const plan = useLoad(() => api.quickWins(), [reloadKey]);
  const p = plan.data;
  if (!p || p.buckets.every((b) => b.count === 0)) return null;
  const cur = p.currency ?? "USD";
  return (
    <Card>
      <CardHeader>
        <CardTitle>Plan</CardTitle>
        <CardDescription>Open savings sorted by effort, risk and how sure CloudCostRadar is.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-3">
        {p.buckets.map((b) => (
          <section key={b.key} aria-label={b.label} className="grid content-start gap-2">
            <div>
              <h3 className="font-medium">{b.label}</h3>
              <p className="text-xs text-muted-foreground">{b.why}</p>
              <p className="text-lg font-semibold tabular-nums">{money(b.savings_monthly, cur)}<span className="text-xs font-normal text-muted-foreground"> / month · {b.count} item(s)</span></p>
            </div>
            <ol className="grid gap-1 text-sm">
              {b.items.slice(0, 5).map((i) => (
                <li key={i.id} className="flex justify-between gap-2">
                  <span className="min-w-0 truncate" title={i.title}>{i.title}</span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">{money(i.savings_monthly, cur)}</span>
                </li>
              ))}
              {b.count > 5 && <li className="text-xs text-muted-foreground">and {b.count - 5} more</li>}
            </ol>
          </section>
        ))}
      </CardContent>
    </Card>
  );
}
