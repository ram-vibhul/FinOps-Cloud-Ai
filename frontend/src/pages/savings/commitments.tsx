import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";
import { money } from "@/lib/format";
import { useLoad } from "@/lib/use-load";

function pct(v: number | null | undefined): string {
  return v == null ? "–" : `${v.toFixed(0)}%`;
}

export function CommitmentsCard({ canRefresh }: { canRefresh: boolean }) {
  const data = useLoad(() => api.commitments(), []);
  const [busy, setBusy] = useState(false);
  const accounts = data.data?.accounts ?? [];
  if (accounts.length === 0 && !canRefresh) return null;

  async function refresh() {
    setBusy(true);
    try {
      await api.refreshCommitments();
      await data.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div>
          <CardTitle className="flex flex-wrap items-center justify-between gap-2">
            Commitments
            <Link to="/savings/commitments" className="text-sm font-normal underline underline-offset-4">
              Every plan, reservation and contract
            </Link>
          </CardTitle>
          <CardDescription>
            How much usage Savings Plans and Reserved Instances cover, and how much of what you already bought is used.
            Purchase suggestions from AWS appear in the list below.
          </CardDescription>
        </div>
        {canRefresh && (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void refresh()}><RefreshCw /> Refresh</Button>
        )}
      </CardHeader>
      <CardContent className="grid gap-4">
        {accounts.length === 0 && <p className="text-sm text-muted-foreground">No snapshot yet: it is taken daily for connected AWS accounts.</p>}
        {accounts.map((a) => (
          <section key={a.connection_id ?? a.name} aria-label={`Commitments for ${a.name}`} className="grid gap-2">
            <p className="text-sm font-medium">{a.name} <span className="font-normal text-muted-foreground">· {new Date(a.taken_at).toLocaleDateString()}</span></p>
            <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
              <div><dt className="text-muted-foreground">Savings Plans coverage</dt><dd className="text-lg font-semibold tabular-nums">{pct(a.savings_plans?.coverage_pct)}</dd></div>
              <div><dt className="text-muted-foreground">Savings Plans used</dt><dd className="text-lg font-semibold tabular-nums">{pct(a.savings_plans?.utilization_pct)}</dd></div>
              <div><dt className="text-muted-foreground">RI coverage</dt><dd className="text-lg font-semibold tabular-nums">{pct(a.reservations?.coverage_pct)}</dd></div>
              <div><dt className="text-muted-foreground">RI used</dt><dd className="text-lg font-semibold tabular-nums">{pct(a.reservations?.utilization_pct)}</dd></div>
              <div><dt className="text-muted-foreground">Saved last 30 days</dt>
                <dd className="text-lg font-semibold tabular-nums">{money((a.savings_plans?.net_savings ?? 0) + (a.reservations?.net_savings ?? 0), "USD")}</dd></div>
            </dl>
          </section>
        ))}
      </CardContent>
    </Card>
  );
}
