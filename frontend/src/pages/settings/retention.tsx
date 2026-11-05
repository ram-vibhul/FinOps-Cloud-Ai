import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage, type RetentionSetting } from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

export function RetentionCard() {
  const me = useMe();
  const { data, setData } = useLoad(() => api.retention(), [me.active_organization_id]);
  if (!data) return null;
  const canEdit = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  return <RetentionForm key={data.map((d) => `${d.kind}:${d.days}`).join()} items={data} canEdit={canEdit} onSaved={setData} />;
}

function RetentionForm({ items, canEdit, onSaved }: {
  items: RetentionSetting[]; canEdit: boolean; onSaved: (v: RetentionSetting[]) => void;
}) {
  const [days, setDays] = useState<Record<string, string>>(
    Object.fromEntries(items.map((i) => [i.kind, String(i.days)])),
  );

  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      onSaved(await api.updateRetention(Object.fromEntries(Object.entries(days).map(([k, v]) => [k, Number(v)]))));
      toast.success("Retention saved. Older data is deleted overnight.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Data retention</CardTitle>
        <CardDescription>How long CostLens keeps each kind of data. Older data is deleted automatically every night.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={(e) => void save(e)} className="grid max-w-md gap-4">
          {items.map((item) => (
            <div key={item.kind} className="grid gap-1">
              <Label htmlFor={`ret-${item.kind}`}>{item.label} (days)</Label>
              <Input id={`ret-${item.kind}`} type="number" min={item.min_days} max={item.max_days}
                value={days[item.kind] ?? ""} disabled={!canEdit}
                onChange={(e) => setDays({ ...days, [item.kind]: e.target.value })} />
              <p className="text-xs text-muted-foreground">
                {item.description} Allowed: {item.min_days}–{item.max_days}; default {item.default_days}.
              </p>
            </div>
          ))}
          {canEdit && <Button type="submit" className="justify-self-start">Save retention</Button>}
        </form>
      </CardContent>
    </Card>
  );
}
