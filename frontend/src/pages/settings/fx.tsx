import { Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type FxOverrideIn } from "@/lib/api";
import { CURRENCIES } from "@/lib/currency";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

function monthRange(month: string): [string, string] {
  const [y = 1970, m = 1] = month.split("-").map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return [`${month}-01`, `${month}-${String(last).padStart(2, "0")}`];
}

export function FxOverridesCard() {
  const me = useMe();
  const canEdit = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.fxOverrides(), [me.active_organization_id]);
  const [from, setFrom] = useState("USD");
  const [to, setTo] = useState("INR");
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [rate, setRate] = useState("");
  const [note, setNote] = useState("");

  async function add(event: FormEvent) {
    event.preventDefault();
    const [start_day, end_day] = monthRange(month);
    const body: FxOverrideIn = { from_currency: from, to_currency: to, start_day, end_day, rate: rate.trim(), note: note.trim() };
    try {
      await api.createFxOverride(body);
      toast.success("Override saved. Reports now use this rate for that month.");
      setRate("");
      setNote("");
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteFxOverride(id);
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Exchange-rate overrides</CardTitle>
        <CardDescription>
          Use your finance team&apos;s rate instead of the daily reference rate (European Central Bank) for a month.
          Overrides work both ways: a USD to INR rate also converts INR to USD.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {(list.data ?? []).length > 0 ? (
          <table className="w-full text-sm">
            <caption className="sr-only">Current overrides</caption>
            <thead className="text-left text-xs text-muted-foreground">
              <tr><th className="font-normal">Period</th><th className="font-normal">Rate</th><th className="font-normal">Note</th><th><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((o) => (
                <tr key={o.id} className="border-t">
                  <td className="py-1.5">{o.start_day} to {o.end_day}</td>
                  <td className="tabular-nums">1 {o.from_currency} = {Number(o.rate).toLocaleString(undefined, { maximumFractionDigits: 6 })} {o.to_currency}</td>
                  <td className="text-muted-foreground">{o.note}</td>
                  <td className="text-right">
                    {canEdit && (
                      <Button variant="ghost" size="icon" aria-label={`Delete override ${o.from_currency} to ${o.to_currency} from ${o.start_day}`}
                        onClick={() => void remove(o.id)}><Trash2 /></Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-muted-foreground">No overrides. Daily reference rates are used.</p>
        )}
        {canEdit && (
          <form onSubmit={(e) => void add(e)} className="grid gap-3 sm:grid-cols-[repeat(4,minmax(0,1fr))_auto] sm:items-end">
            <div className="grid gap-1">
              <Label htmlFor="fx-month">Month</Label>
              <Input id="fx-month" type="month" value={month} onChange={(e) => setMonth(e.target.value)} required />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="fx-from">1 unit of</Label>
              <NativeSelect id="fx-from" value={from} onChange={(e) => setFrom(e.target.value)}>
                {CURRENCIES.map((c) => <option key={c}>{c}</option>)}
              </NativeSelect>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="fx-rate">equals</Label>
              <Input id="fx-rate" inputMode="decimal" pattern="[0-9]*[.]?[0-9]+" placeholder="84.50" value={rate} onChange={(e) => setRate(e.target.value)} required />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="fx-to">of</Label>
              <NativeSelect id="fx-to" value={to} onChange={(e) => setTo(e.target.value)}>
                {CURRENCIES.map((c) => <option key={c}>{c}</option>)}
              </NativeSelect>
            </div>
            <Button type="submit">Add</Button>
            <div className="grid gap-1 sm:col-span-4">
              <Label htmlFor="fx-note">Note (optional)</Label>
              <Input id="fx-note" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="Finance rate for month-end close" />
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
