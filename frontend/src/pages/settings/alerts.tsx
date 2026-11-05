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
              Word anomaly explanations with AI
              <span className="block text-xs text-muted-foreground">
                Sends the short summary (names and amounts, never billing files) to Anthropic. Needs an AI key on this installation; otherwise the standard wording is used.
              </span>
            </span>
          </label>
          {canEdit && <Button type="submit" className="justify-self-start">Save alerts</Button>}
        </form>
      </CardContent>
    </Card>
  );
}
