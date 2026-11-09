import { type FormEvent, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage } from "@/lib/api";
import { useMe, useSession } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

/** A partner's invitation: put an organisation you own under the partner, or start a new one. */
export function PartnerInvitePage() {
  const { code = "" } = useParams();
  const me = useMe();
  const { refresh } = useSession();
  const navigate = useNavigate();
  const info = useLoad(() => api.inviteInfo(code), [code]);
  const owned = me.organizations.filter((o) => o.role === "owner" && !o.is_partner && !o.partner_id && !o.is_demo);
  const [choice, setChoice] = useState(owned[0]?.id ?? "new");
  const [name, setName] = useState("");

  async function accept(e: FormEvent) {
    e.preventDefault();
    try {
      const out = await api.acceptInvite(code, choice === "new" ? { new_name: name.trim() } : { organization_id: choice });
      toast.success(`${out.partner} can now help manage this organisation.`);
      await refresh();
      navigate("/");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  if (info.error) {
    return <p className="p-6 text-sm">This invitation is not valid or has expired. Ask your partner for a new link.</p>;
  }
  return (
    <div className="mx-auto max-w-lg p-6">
      <Card>
        <CardHeader>
          {info.data?.logo && <img src={info.data.logo} alt="" className="mb-2 h-10 w-auto self-start" />}
          <CardTitle>{info.data ? `${info.data.partner} invites you` : "Partner invitation"}</CardTitle>
          <CardDescription>They will get admin access to the organisation you choose, to help manage its cloud costs. You can remove their access at any time under Settings → General.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={(e) => void accept(e)} className="grid gap-3 text-sm">
            <div className="grid gap-1"><Label htmlFor="pi-org">Organisation</Label>
              <NativeSelect id="pi-org" value={choice} onChange={(e) => setChoice(e.target.value)}>
                {owned.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                <option value="new">A new organisation…</option>
              </NativeSelect></div>
            {choice === "new" && (
              <div className="grid gap-1"><Label htmlFor="pi-name">Name</Label>
                <Input id="pi-name" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} /></div>
            )}
            <Button type="submit">Accept invitation</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
