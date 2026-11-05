import { createContext, use, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, api, type Me, type Organization, setCsrfToken } from "@/lib/api";

type SessionState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "error"; message: string }
  | { status: "ready"; me: Me };

interface SessionValue {
  state: SessionState;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: "loading" });

  const refresh = useCallback(async () => {
    try {
      const me = await api.me();
      setCsrfToken(me.csrf_token);
      setState({ status: "ready", me });
    } catch (error) {
      setCsrfToken(undefined);
      if (error instanceof ApiError && error.status === 401) setState({ status: "anonymous" });
      else setState({ status: "error", message: "Could not reach CostLens. Check your connection." });
    }
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setCsrfToken(undefined);
      setState({ status: "anonymous" });
    }
  }, []);

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- loading data from the API
    void refresh();
  }, [refresh]);

  const value = useMemo(() => ({ state, refresh, signOut }), [state, refresh, signOut]);
  return <SessionContext value={value}>{children}</SessionContext>;
}

export function useSession(): SessionValue {
  const ctx = use(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}

/** The signed-in user; only call below <RequireSession>. */
export function useMe(): Me {
  const { state } = useSession();
  if (state.status !== "ready") throw new Error("useMe needs a signed-in session");
  return state.me;
}

export function activeOrg(me: Me): Organization | undefined {
  return me.organizations.find((o) => o.id === me.active_organization_id);
}

const RANK = { viewer: 0, member: 1, admin: 2, owner: 3 } as const;

export function atLeast(role: Me["role"], needed: keyof typeof RANK): boolean {
  return role !== null && RANK[role] >= RANK[needed];
}
