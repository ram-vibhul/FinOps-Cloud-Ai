import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { useLoad } from "@/lib/use-load";

function monthName(m: string): string {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y!, (mo ?? 1) - 1, 1).toLocaleDateString(undefined, { month: "short", year: "numeric" });
}

export function KeptCard() {
  const data = useLoad(() => api.keptSavings(), []);
  const k = data.data;
  if (!k || (k.months.length === 0 && k.regressions.length === 0)) return null;
  const cur = k.currency ?? "USD";
  const top = Math.max(...k.months.map((m) => m.kept), 1);
  return (
    <Card>
      <CardHeader>
        <CardTitle>What you kept</CardTitle>
        <CardDescription>Verified savings, rechecked on the bill every month.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          {k.latest_month && (
            <p>
              <span className="text-2xl font-semibold tabular-nums">{money(k.kept_latest_month, cur)}</span>
              <span className="text-sm text-muted-foreground"> kept in {monthName(k.latest_month)}</span>
            </p>
          )}
          <ul className="grid gap-1 text-xs" aria-label="Kept per month">
            {k.months.map((m) => (
              <li key={m.month} className="grid grid-cols-[5rem_1fr_6rem] items-center gap-2">
                <span className="text-muted-foreground">{monthName(m.month)}</span>
                <span className="h-2 rounded-full bg-primary" style={{ width: `${Math.max(2, (100 * m.kept) / top)}%` }} />
                <span className="text-right tabular-nums">{money(m.kept, cur)}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="grid content-start gap-1 text-sm">
          <p className="font-medium">Savings that came back</p>
          {k.regressions.length === 0 ? (
            <p className="text-muted-foreground">None: every verified saving is holding.</p>
          ) : (
            <ul className="grid gap-1" aria-label="Savings that came back">
              {k.regressions.map((r) => (
                <li key={r.id}>{r.title} <span className="text-muted-foreground">· {new Date(r.regressed_at).toLocaleDateString()}</span></li>
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
