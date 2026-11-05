import { Download, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api";
import { activeOrg, useMe, useSession } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

/** Owner-only: export all organisation data, and delete the organisation. */
export function OwnerZone() {
  const me = useMe();
  const org = activeOrg(me);
  const exports = useLoad(() => api.exports(), [me.active_organization_id]);

  async function requestExport() {
    try {
      await api.requestExport();
      toast.success("Export started. It appears below when ready.");
      await exports.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  if (!org || org.is_demo) return null;
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Export organisation data</CardTitle>
          <CardDescription>A ZIP file with every record of this organisation. Downloads expire after 7 days.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <Button variant="outline" className="self-start" onClick={() => void requestExport()}>
            <Download /> Create export
          </Button>
          <ul className="flex flex-col gap-1 text-sm">
            {(exports.data ?? []).map((e) => (
              <li key={e.id} className="flex flex-wrap items-center gap-2">
                <span>{new Date(e.created_at).toLocaleString()}</span>
                {e.download_url ? (
                  <a className="font-medium underline underline-offset-4" href={e.download_url} download>
                    Download ({Math.max(1, Math.round((e.size_bytes ?? 0) / 1024))} KB)
                  </a>
                ) : (
                  <span className="text-muted-foreground">{e.status === "failed" ? "failed" : "preparing…"}</span>
                )}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
      <Card className="border-destructive/40">
        <CardHeader>
          <CardTitle>Delete organisation</CardTitle>
          <CardDescription>
            Everyone loses access immediately. Owners can restore it for 30 days; after that all its data is
            permanently deleted.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DeleteOrgDialog slug={org.slug} name={org.name} />
        </CardContent>
      </Card>
    </>
  );
}

function DeleteOrgDialog({ slug, name }: { slug: string; name: string }) {
  const { refresh } = useSession();
  const navigate = useNavigate();
  const [typed, setTyped] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      const r = await api.deleteOrg(typed);
      toast.success(`${name} will be permanently deleted after ${new Date(r.purge_after).toLocaleDateString()}.`);
      await refresh();
      navigate("/", { replace: true });
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="destructive">
          <Trash2 /> Delete organisation
        </Button>
      </DialogTrigger>
      <DialogContent title={`Delete ${name}?`} description="Owners can restore it within 30 days.">
        <form onSubmit={(e) => void submit(e)} className="grid gap-3">
          <Label htmlFor="confirm-slug">Type the organisation address ({slug}) to confirm</Label>
          <Input id="confirm-slug" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          <Button type="submit" variant="destructive" disabled={typed.trim().toLowerCase() !== slug}>
            Delete organisation
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
