import { MailCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import { PublicLayout } from "@/app/public-layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage, type InvitationPreview } from "@/lib/api";
import { useSession } from "@/lib/session";

export function InvitePage() {
  const { token = "" } = useParams();
  const { state, refresh, signOut } = useSession();
  const navigate = useNavigate();
  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.previewInvitation(token).then(setPreview, () => setNotFound(true));
  }, [token]);

  async function accept() {
    setBusy(true);
    setProblem(null);
    try {
      await api.acceptInvitation(token);
      await refresh();
      toast.success(`You joined ${preview?.organization ?? "the organisation"}.`);
      navigate("/", { replace: true });
    } catch (error) {
      setProblem(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const signInHref = `/sign-in?return_to=${encodeURIComponent(`/invite/${token}`)}`;

  return (
    <PublicLayout>
      <Card>
        <CardHeader>
          <MailCheck className="size-8 text-primary" aria-hidden />
          <CardTitle className="text-xl">
            {notFound ? "Invitation not found" : preview ? `Join ${preview.organization}` : "Loading invitation…"}
          </CardTitle>
          {preview && (
            <CardDescription>
              {preview.invited_by ?? "Someone"} invited {preview.email_hint} to join as {preview.role}.
            </CardDescription>
          )}
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {notFound && <p className="text-sm text-muted-foreground">This link is not valid. Ask for a new invitation.</p>}
          {preview && preview.status !== "pending" && (
            <p role="alert" className="text-sm text-destructive">
              This invitation is {preview.status}. Ask for a new one.
            </p>
          )}
          {problem && (
            <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {problem}
            </p>
          )}
          {preview?.status === "pending" &&
            (state.status === "ready" ? (
              <>
                <p className="text-sm text-muted-foreground">Signed in as {state.me.user.email}.</p>
                <Button onClick={() => void accept()} disabled={busy}>
                  Accept invitation
                </Button>
                <Button variant="ghost" onClick={() => void signOut()}>
                  Use a different account
                </Button>
              </>
            ) : (
              <Button asChild>
                <a href={signInHref}>Sign in to accept</a>
              </Button>
            ))}
        </CardContent>
      </Card>
    </PublicLayout>
  );
}
