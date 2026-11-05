import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";
import { FullPageSpinner } from "@/components/spinner";
import { Button } from "@/components/ui/button";
import { useSession } from "@/lib/session";

/** Gate for signed-in pages. ``needsOrg``: send people without an organisation to /welcome. */
export function RequireSession({ children, needsOrg = true }: { children: ReactNode; needsOrg?: boolean }) {
  const { state, refresh } = useSession();
  const location = useLocation();
  if (state.status === "loading") return <FullPageSpinner />;
  if (state.status === "error") {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-3 p-4 text-center">
        <p className="text-sm text-muted-foreground">{state.message}</p>
        <Button variant="outline" onClick={() => void refresh()}>
          Try again
        </Button>
      </div>
    );
  }
  if (state.status === "anonymous") {
    const here = location.pathname + location.search;
    const q = here !== "/" ? `?return_to=${encodeURIComponent(here)}` : "";
    return <Navigate to={`/sign-in${q}`} replace />;
  }
  if (needsOrg && !state.me.active_organization_id) return <Navigate to="/welcome" replace />;
  return <>{children}</>;
}
