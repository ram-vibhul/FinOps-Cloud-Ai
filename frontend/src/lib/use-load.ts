import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { errorMessage } from "@/lib/api";

/**
 * Load data on mount, and again whenever ``deps`` change, with loading/error state
 * and a manual ``reload``.
 */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  useLayoutEffect(() => {
    loadRef.current = load;
  });
  const key = JSON.stringify(deps);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // `key` re-runs the load when the caller's inputs (e.g. the active organisation) change.
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- loading data from the API
    void reload();
    // oxlint-disable-next-line react/exhaustive-effect-dependencies -- `key` is the trigger
  }, [reload, key]);

  return { data, error, loading, reload, setData };
}
