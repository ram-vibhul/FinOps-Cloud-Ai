import { AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { useLoad } from "@/lib/use-load";

export function EarlyWarnings({ currency, reloadKey }: { currency: string; reloadKey: unknown }) {
  const data = useLoad(() => api.earlyWarnings(), [reloadKey]);
  const items = data.data ?? [];
  if (items.length === 0) return null;
  return (
    <output aria-label="Early warnings" className="grid gap-2 rounded-lg border border-warning/50 bg-warning/10 p-3 text-sm">
      {items.map((w) => (
        <p key={w.kind} className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            {w.kind === "forecast"
              ? <>Heading for <b>{money(w.forecast ?? 0, currency)}</b> this month, {w.over_pct}% above last month ({money(w.last_month ?? 0, currency)}).</>
              : <>Spend today is <b>{w.over_pct}% above normal</b>: {money(w.so_far ?? 0, currency)} in {w.hours} hours vs a usual {money(w.usual ?? 0, currency)}.</>}
            {w.drivers.length > 0 && <span className="text-muted-foreground"> Driven by {w.drivers.map((d) => d.key).join(", ")}.</span>}
          </span>
        </p>
      ))}
    </output>
  );
}
