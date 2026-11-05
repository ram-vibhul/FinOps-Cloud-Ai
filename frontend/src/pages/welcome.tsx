import { Compass, Plus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { PublicLayout } from "@/app/public-layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api";
import { useMe, useSession } from "@/lib/session";

export function WelcomePage() {
  const me = useMe();
  const { refresh, signOut } = useSession();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await action();
      await refresh();
      toast.success(done);
      navigate("/", { replace: true });
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function create(event: FormEvent) {
    event.preventDefault();
    void run(() => api.createOrg(name.trim()), `${name.trim()} is ready.`);
  }

  return (
    <PublicLayout>
      <Card>
        <CardHeader>
          <CardTitle className="text-xl">Welcome, {me.user.name || me.user.email}</CardTitle>
          <CardDescription>
            Create an organisation for your company, or look around the demo first. You can do both.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          <form onSubmit={create} className="flex flex-col gap-2">
            <Label htmlFor="org-name">Organisation name</Label>
            <Input
              id="org-name"
              placeholder="Acme Ltd"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              required
            />
            <p className="text-xs text-muted-foreground">Usually your company name. You can change it later.</p>
            <Button type="submit" disabled={busy || !name.trim()}>
              <Plus /> Create organisation
            </Button>
          </form>
          <div className="border-t pt-4">
            <Button variant="outline" className="w-full" disabled={busy}
              onClick={() => void run(() => api.joinDemo(), "Welcome to the demo. Everything here is sample data.")}>
              <Compass /> Explore the demo
            </Button>
            <p className="mt-2 text-xs text-muted-foreground">
              A sample company with realistic data. Read-only; nothing to connect.
            </p>
          </div>
          {me.organizations.length > 0 && (
            <p className="text-sm text-muted-foreground">
              You already belong to {me.organizations.length} organisation(s); choose one from the menu after creating or
              joining.
            </p>
          )}
          <Button variant="ghost" onClick={() => void signOut()}>
            Sign out
          </Button>
        </CardContent>
      </Card>
    </PublicLayout>
  );
}
