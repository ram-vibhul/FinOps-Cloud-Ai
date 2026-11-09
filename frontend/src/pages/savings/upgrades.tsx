import { ArrowUpCircle } from "lucide-react";
import { Link } from "react-router";
import { EmptyState } from "@/components/empty-state";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

export function UpgradesPage() {
  const me = useMe();
  const data = useLoad(() => api.upgrades(), [me.active_organization_id]);
  const d = data.data;
  const cur = d?.items[0]?.currency ?? "USD";
  return (
    <div className="flex flex-col gap-6">
      <div>
        <p className="text-sm text-muted-foreground"><Link to="/savings" className="underline underline-offset-4">Savings</Link> /</p>
        <h1 className="text-2xl font-semibold tracking-tight">Upgrades</h1>
        <p className="text-sm text-muted-foreground">What staying on old versions and generations costs, and what to move to.</p>
      </div>
      {d && d.items.length === 0 && d.coming_up.length === 0 && (
        <EmptyState icon={ArrowUpCircle} title="Nothing to upgrade">No extended-support charges or old generations found.</EmptyState>
      )}
      {d && d.items.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Cost of staying: {money(d.staying_monthly, cur)} a month</CardTitle>
            <CardDescription>Extended-support charges and older, pricier generations you could leave behind.</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <THead><TR><TH>What</TH><TH>Move to</TH><TH>Standard support ended</TH><TH className="text-right">Staying costs</TH></TR></THead>
              <TBody>
                {d.items.map((i) => (
                  <TR key={i.id}>
                    <TD className="font-medium">{i.title}</TD>
                    <TD>{i.upgrade_to ?? "–"}</TD>
                    <TD>{i.standard_end ? new Date(i.standard_end).toLocaleDateString() : "–"}</TD>
                    <TD className="text-right tabular-nums">
                      {money(i.staying_monthly, i.currency)}
                      <span className="block text-xs text-muted-foreground">{i.basis === "billing" ? "from your bill" : "estimate"}</span>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}
      {d && d.coming_up.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Coming up in the next 6 months</CardTitle>
            <CardDescription>Standard support ends soon; upgrading before then avoids the charge.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y text-sm" aria-label="Coming up">
              {d.coming_up.map((c) => (
                <li key={`${c.kind}${c.resource}`} className="flex flex-wrap justify-between gap-2 py-2">
                  <span><span className="font-medium">{c.kind} {c.resource}</span> ({c.version}) → {c.upgrade_to}</span>
                  <span className="text-muted-foreground">
                    from {new Date(c.ends).toLocaleDateString()}
                    {c.estimated_monthly_usd != null && ` · about ${money(c.estimated_monthly_usd, "USD")} a month`}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
