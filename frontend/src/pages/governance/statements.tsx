import { Download, Lock, Unlock } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

function lastMonth(): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function StatementsTab() {
  const me = useMe();
  const isAdmin = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const [month, setMonth] = useState(lastMonth());
  const st = useLoad(() => api.statement(month), [me.active_organization_id, month]);
  const [inv, setInv] = useState({ invoice_id: "", amount: "", billing_account_id: "" });
  const s = st.data;

  async function act(run: () => Promise<unknown>, done: string) {
    try {
      await run();
      toast.success(done);
      await st.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function addInvoice(event: FormEvent) {
    event.preventDefault();
    await act(() => api.addInvoice(month, { invoice_id: inv.invoice_id.trim(), amount: Number(inv.amount), currency: s?.currency ?? "USD",
      billing_account_id: inv.billing_account_id.trim() || undefined }), "Invoice added.");
    setInv({ invoice_id: "", amount: "", billing_account_id: "" });
  }

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            Statement {s?.closed && <Badge variant="secondary"><Lock className="size-3" /> closed</Badge>}
          </CardTitle>
          <CardDescription>
            {s?.closed ? `Closed ${new Date(s.closed.at).toLocaleDateString()}${s.closed.note ? `: ${s.closed.note}` : ""}. Re-imports for this month are skipped until it is reopened.`
              : "Close a month once finance has signed it off: its numbers are frozen and re-imports are skipped."}
          </CardDescription>
          <div className="flex flex-wrap items-end gap-2">
            <div className="grid gap-1">
              <Label htmlFor="st-month">Month</Label>
              <Input id="st-month" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
            </div>
            <Button variant="outline" asChild><a href={`/api/v1/statements/${month}/statement.csv`} download><Download /> CSV</a></Button>
            <Button variant="outline" asChild title="Every line item for the month, with FinOps FOCUS column names">
              <a href={`/api/v1/statements/${month}/focus.csv`} download><Download /> FOCUS export</a>
            </Button>
            {isAdmin && s && !s.closed && (
              <Button variant="outline" onClick={() => { const note = window.prompt("Note for the close (optional)") ?? ""; void act(() => api.closeMonth(month, note), "Month closed."); }}>
                <Lock /> Close month
              </Button>
            )}
            {isAdmin && s?.closed && (
              <Button variant="ghost" onClick={() => void act(() => api.reopenMonth(month), "Month reopened.")}><Unlock /> Reopen</Button>
            )}
          </div>
        </CardHeader>
        {s && (
          <CardContent className="grid gap-4 md:grid-cols-3">
            {(["by_category", "by_account", "by_service"] as const).map((k) => (
              <section key={k} aria-label={k.replace("by_", "By ")} className="grid content-start gap-1 text-sm">
                <h3 className="font-medium capitalize">{k.replace("by_", "By ")}</h3>
                {s[k].map((r) => (
                  <p key={r.key} className="flex justify-between gap-2"><span className="truncate">{r.key}</span><span className="tabular-nums">{money(r.cost, s.currency)}</span></p>
                ))}
              </section>
            ))}
            <p className="font-medium md:col-span-3">Total {money(s.total, s.currency)}</p>
          </CardContent>
        )}
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Invoices</CardTitle>
          <CardDescription>Each invoice is compared with the billed cost of its billing account; within 1 or 0.5% it matches.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          {s && s.invoices.length > 0 && (
            <Table>
              <THead><TR><TH>Invoice</TH><TH className="text-right">Invoice</TH><TH className="text-right">Bill</TH><TH className="text-right">Difference</TH><TH>Result</TH></TR></THead>
              <TBody>
                {s.invoices.map((i) => (
                  <TR key={i.id}>
                    <TD>{i.invoice_id}<span className="block text-xs text-muted-foreground">{[i.billing_account_id, i.source].filter(Boolean).join(" · ")}</span></TD>
                    <TD className="text-right tabular-nums">{money(i.amount, i.currency)}</TD>
                    <TD className="text-right tabular-nums">{money(i.billed, i.currency)}</TD>
                    <TD className="text-right tabular-nums">{money(i.difference, i.currency)}</TD>
                    <TD><Badge variant={i.status === "matched" ? "default" : "destructive"}>{i.status}</Badge></TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
          {isAdmin && (
            <form onSubmit={(e) => void addInvoice(e)} aria-label="Add invoice" className="flex flex-wrap items-end gap-2">
              <div className="grid gap-1"><Label htmlFor="inv-id">Invoice number</Label>
                <Input id="inv-id" required value={inv.invoice_id} onChange={(e) => setInv({ ...inv, invoice_id: e.target.value })} /></div>
              <div className="grid gap-1"><Label htmlFor="inv-amount">Total ({s?.currency ?? "USD"})</Label>
                <Input id="inv-amount" type="number" step="0.01" required value={inv.amount} onChange={(e) => setInv({ ...inv, amount: e.target.value })} /></div>
              <div className="grid gap-1"><Label htmlFor="inv-account">Billing account</Label>
                <Input id="inv-account" value={inv.billing_account_id} onChange={(e) => setInv({ ...inv, billing_account_id: e.target.value })} /></div>
              <Button type="submit">Add invoice</Button>
              <Button type="button" variant="outline" onClick={() => void act(() => api.fetchInvoices(month), "Invoices read from AWS.")}>Read from AWS</Button>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
