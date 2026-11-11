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
import { useMe, useSession } from "@/lib/session";

export function AccountTab() {
  const me = useMe();
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Your data</CardTitle>
          <CardDescription>
            Download everything CloudCostRadar stores about you: profile, sign-in methods, organisations, sessions and your
            actions.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild variant="outline">
            <a href="/api/v1/me/export" download>
              <Download /> Download my data
            </a>
          </Button>
        </CardContent>
      </Card>
      <Card className="border-destructive/40">
        <CardHeader>
          <CardTitle>Delete your account</CardTitle>
          <CardDescription>
            Removes your profile and sign-in methods and takes you out of every organisation. Your name is removed from
            audit history. If you are the only owner of an organisation, transfer ownership or delete it first.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DeleteAccountDialog email={me.user.email} />
        </CardContent>
      </Card>
    </div>
  );
}

function DeleteAccountDialog({ email }: { email: string }) {
  const { signOut } = useSession();
  const navigate = useNavigate();
  const [typed, setTyped] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await api.deleteMe(typed);
      toast.success("Your account was deleted.");
      await signOut().catch(() => undefined);
      navigate("/sign-in", { replace: true });
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="destructive">
          <Trash2 /> Delete my account
        </Button>
      </DialogTrigger>
      <DialogContent title="Delete your account?" description="This cannot be undone.">
        <form onSubmit={(e) => void submit(e)} className="grid gap-3">
          <Label htmlFor="confirm-email">Type your email address ({email}) to confirm</Label>
          <Input id="confirm-email" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          <Button type="submit" variant="destructive" disabled={typed.trim().toLowerCase() !== email}>
            Delete my account
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
