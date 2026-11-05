import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type OrgDetails } from "@/lib/api";
import { activeOrg, atLeast, useMe, useSession } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const CURRENCIES = ["USD", "INR", "EUR", "GBP", "AUD", "CAD", "SGD", "JPY", "AED"];

export function GeneralTab() {
  const me = useMe();
  const { data: org } = useLoad(() => api.org(), [me.active_organization_id]);
  if (!org) return null;
  // Keyed by organisation so the form starts from fresh values after switching.
  return <GeneralForm key={org.id} org={org} />;
}

function GeneralForm({ org }: { org: OrgDetails }) {
  const me = useMe();
  const { refresh } = useSession();
  const [name, setName] = useState(org.name);
  const [currency, setCurrency] = useState(org.default_currency);
  const canEdit = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;

  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      await api.updateOrg({ name: name.trim(), default_currency: currency });
      await refresh();
      toast.success("Saved.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Organisation</CardTitle>
        <CardDescription>The display currency is used for reports unless someone picks another.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={(e) => void save(e)} className="grid max-w-md gap-4">
          <div className="grid gap-2">
            <Label htmlFor="org-name">Name</Label>
            <Input id="org-name" value={name} onChange={(e) => setName(e.target.value)} disabled={!canEdit} required />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="org-currency">Display currency</Label>
            <NativeSelect id="org-currency" value={currency} onChange={(e) => setCurrency(e.target.value)} disabled={!canEdit}>
              {CURRENCIES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </NativeSelect>
          </div>
          <p className="text-xs text-muted-foreground">Address: {org.slug}</p>
          {canEdit && (
            <Button type="submit" className="justify-self-start">
              Save
            </Button>
          )}
        </form>
      </CardContent>
    </Card>
  );
}
