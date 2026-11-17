import { useEffect, useLayoutEffect, useRef } from "react";

/**
 * Call ``fn`` every ``ms`` while the tab is visible (11.16). Hidden tabs do not poll;
 * when the tab becomes visible again, ``fn`` runs at once and polling resumes.
 * Pass ``enabled = false`` to stop.
 */
export function usePoll(fn: () => void, ms: number, enabled = true): void {
  const fnRef = useRef(fn);
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  useLayoutEffect(() => {
    fnRef.current = fn;
  });
  useEffect(() => {
    if (!enabled) return;
    const start = () => {
      if (timerRef.current === undefined) timerRef.current = setInterval(() => fnRef.current(), ms);
    };
    const stop = () => {
      if (timerRef.current !== undefined) clearInterval(timerRef.current);
      timerRef.current = undefined;
    };
    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        fnRef.current();
        start();
      }
    };
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [ms, enabled]);
}
