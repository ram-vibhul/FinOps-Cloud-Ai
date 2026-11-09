import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";
import { useLoad } from "@/lib/use-load";

const SCOPE: Record<string, string> = {
  finding: "this finding", resource: "resource", tag: "tag", account: "account", check: "everywhere",
};

export function RulesCard({ canEdit, onChanged }: { canEdit: boolean; onChanged: () => void }) {
  const rules = useLoad(() => api.suppressions(), []);
  const noise = useLoad(() => api.findingNoise(), []);
  const rows = rules.data ?? [];
  const noisy = (noise.data ?? []).filter((n) => n.dismissed > 0);
  if (rows.length === 0 && noisy.length === 0) return null;

  async function remove(id: string) {
    try {
      const r = await api.deleteSuppression(id);
      toast.success(`Rule removed; ${r.reopened} finding(s) reopened.`);
      await Promise.all([rules.reload(), noise.reload()]);
      onChanged();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Dismissed and noisy checks</CardTitle>
        <CardDescription>What your team said doesn't apply, and which checks are dismissed most often.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 text-sm">
        {rows.length > 0 && (
          <ul className="divide-y" aria-label="Dismissal rules">
            {rows.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <span className="font-medium">{r.check}</span> — {r.reason_label}
                  {r.scope !== "finding" && <> for {SCOPE[r.scope]}{r.value ? ` ${r.value}` : ""}</>}
                  {r.note && <span className="text-muted-foreground"> · {r.note}</span>}
                  <span className="text-muted-foreground"> · {r.findings} finding(s)</span>
                </span>
                {canEdit && <Button size="sm" variant="ghost" onClick={() => void remove(r.id)}>Remove rule</Button>}
              </li>
            ))}
          </ul>
        )}
        {noisy.length > 0 && (
          <ul className="grid gap-1" aria-label="Noise per check">
            {noisy.slice(0, 10).map((n) => (
              <li key={n.detector}>
                {n.title}: {n.dismissed} dismissed, {n.acted} acted on
                {n.dismissed_pct != null && ` (${n.dismissed_pct}% dismissed)`}
                {n.noisy && <Badge variant="warning" className="ml-2">noisy</Badge>}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
