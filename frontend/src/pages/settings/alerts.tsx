import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { type AlertSettings, api, errorMessage } from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

export function AlertSettingsCard() {
  const me = useMe();
  const { data } = useLoad(() => api.alertSettings(), [me.active_organization_id]);
  if (!data) return null;
  const canEdit = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  return <AlertForm key={JSON.stringify(data)} initial={data} canEdit={canEdit} />;
}

function AlertForm({ initial, canEdit }: { initial: AlertSettings; canEdit: boolean }) {
  const [form, setForm] = useState(initial);
  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      setForm(await api.saveAlertSettings(form));
      toast.success("Alert settings saved.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Cost alerts</CardTitle>
        <CardDescription>Owners and admins get an email when spend jumps above its usual pattern.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={(e) => void save(e)} className="grid max-w-md gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.anomaly_alerts} disabled={!canEdit}
              onChange={(e) => setForm({ ...form, anomaly_alerts: e.target.checked })} />
            Email unusual cost increases
          </label>
          <div className="grid gap-1">
            <Label htmlFor="alert-sensitivity">Sensitivity</Label>
            <NativeSelect id="alert-sensitivity" value={form.sensitivity} disabled={!canEdit}
              onChange={(e) => setForm({ ...form, sensitivity: e.target.value as AlertSettings["sensitivity"] })}>
              <option value="low">Low — only big surprises</option>
              <option value="medium">Medium</option>
              <option value="high">High — catch smaller changes</option>
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="alert-min">Ignore increases smaller than (per day)</Label>
            <Input id="alert-min" type="number" min={0} value={form.min_impact} disabled={!canEdit}
              onChange={(e) => setForm({ ...form, min_impact: Number(e.target.value) })} />
          </div>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={form.ai_explanations ?? false} disabled={!canEdit}
              onChange={(e) => setForm({ ...form, ai_explanations: e.target.checked })} />
            <span>
              Use AI for anomaly explanations, Kubernetes investigations and plain-words questions in the report builder
              <span className="block text-xs text-muted-foreground">
                Sends short summaries (names, amounts and findings, with secrets and personal data removed; never billing files or logs) to Anthropic. Report-builder questions send the question plus dimension, tag, service and region names, never amounts. Needs an AI key on this installation; otherwise the standard wording is used.
              </span>
            </span>
          </label>
          <fieldset className="grid gap-2 text-sm" disabled={!canEdit}>
            <legend className="font-medium">Early warnings</legend>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={form.forecast_warnings ?? true} onChange={(e) => setForm({ ...form, forecast_warnings: e.target.checked })} />
              Warn when the month-end forecast is more than
              <Input aria-label="Forecast over last month (%)" type="number" min={1} max={500} className="h-7 w-20" value={form.forecast_over_pct ?? 10}
                onChange={(e) => setForm({ ...form, forecast_over_pct: Number(e.target.value) })} />
              % above last month
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={form.same_day_warnings ?? true} onChange={(e) => setForm({ ...form, same_day_warnings: e.target.checked })} />
              Warn when today's spend runs more than
              <Input aria-label="Today over usual (%)" type="number" min={5} max={1000} className="h-7 w-20" value={form.same_day_over_pct ?? 30}
                onChange={(e) => setForm({ ...form, same_day_over_pct: Number(e.target.value) })} />
              % above the usual (needs hourly data from the billing export)
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={form.weekly_digest_email ?? false} disabled={!canEdit}
                onChange={(e) => setForm({ ...form, weekly_digest_email: e.target.checked })} />
              Email a weekly digest to owners and admins on Monday mornings (Slack and Teams channels choose it under Notifications)
            </label>
          </fieldset>
          {canEdit && <Button type="submit" className="justify-self-start">Save alerts</Button>}
        </form>
      </CardContent>
    </Card>
  );
}
