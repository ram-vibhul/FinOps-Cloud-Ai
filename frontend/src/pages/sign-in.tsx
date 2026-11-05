import { Building2, LogIn } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";
import { PublicLayout } from "@/app/public-layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage, loginUrl, type Provider } from "@/lib/api";
import { signInErrorMessage } from "@/lib/auth-errors";
import { useSession } from "@/lib/session";

function LocalLoginForm({ returnTo }: { returnTo: string }) {
  const { refresh } = useSession();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await api.localLogin(email.trim(), "");
      await refresh();
      navigate(returnTo, { replace: true });
    } catch (error) {
      setProblem(errorMessage(error));
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-2 rounded-lg border border-dashed p-3">
      <Label htmlFor="local-email">Local account (development only)</Label>
      <div className="flex gap-2">
        <Input id="local-email" type="email" placeholder="you@example.com" value={email}
          onChange={(e) => setEmail(e.target.value)} required />
        <Button type="submit" variant="secondary">Continue</Button>
      </div>
      {problem && <p role="alert" className="text-sm text-destructive">{problem}</p>}
    </form>
  );
}

export function SignInPage() {
  const { state } = useSession();
  const [params] = useSearchParams();
  const returnTo = params.get("return_to") ?? "/";
  const error = signInErrorMessage(params.get("error"));
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [slug, setSlug] = useState("");
  const [orgProviders, setOrgProviders] = useState<Provider[] | null>(null);
  const [orgLookupFailed, setOrgLookupFailed] = useState(false);

  useEffect(() => {
    api.providers().then(setProviders, () => setProviders([]));
  }, []);

  if (state.status === "ready") return <Navigate to={returnTo} replace />;

  async function findOrg(event: FormEvent) {
    event.preventDefault();
    setOrgLookupFailed(false);
    try {
      const found = await api.orgProviders(slug.trim().toLowerCase());
      setOrgProviders(found);
      setOrgLookupFailed(found.length === 0);
    } catch {
      setOrgLookupFailed(true);
    }
  }

  return (
    <PublicLayout>
      <Card>
        <CardHeader>
          <CardTitle className="text-xl">Sign in to CostLens</CardTitle>
          <CardDescription>See where your cloud money goes and fix problems faster.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {error && (
            <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </p>
          )}
          {providers === null ? (
            <p className="text-sm text-muted-foreground">Loading sign-in options…</p>
          ) : providers.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No sign-in provider is configured yet. Your administrator needs to set one up.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {providers.some((p) => p.id === "local") && <LocalLoginForm returnTo={returnTo} />}
              {providers.filter((p) => p.id !== "local").map((p) => (
                <Button key={p.id} asChild variant="outline" size="lg">
                  <a href={loginUrl(p.id, returnTo)}>
                    <LogIn /> Continue with {p.name}
                  </a>
                </Button>
              ))}
            </div>
          )}
          <div className="border-t pt-4">
            <form onSubmit={(e) => void findOrg(e)} className="flex flex-col gap-2">
              <Label htmlFor="org-slug">Company single sign-on</Label>
              <div className="flex gap-2">
                <Input
                  id="org-slug"
                  placeholder="your-company"
                  value={slug}
                  onChange={(e) => setSlug(e.target.value)}
                  autoComplete="organization"
                />
                <Button type="submit" variant="secondary" disabled={!slug.trim()}>
                  <Building2 /> Find
                </Button>
              </div>
              {orgLookupFailed && (
                <p className="text-sm text-muted-foreground">No single sign-on found for that organisation.</p>
              )}
              {orgProviders?.map((p) => (
                <Button key={p.id} asChild variant="outline">
                  <a href={loginUrl(p.id, returnTo)}>Continue with {p.name}</a>
                </Button>
              ))}
            </form>
          </div>
        </CardContent>
      </Card>
    </PublicLayout>
  );
}
