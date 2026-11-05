import { useCallback, useState } from "react";

export const CURRENCIES = ["USD", "INR", "EUR", "GBP", "AUD", "CAD", "SGD", "JPY", "AED", "CHF", "SEK", "NZD"];
const KEY = "costlens-display-currency";

function read(): string | undefined {
  try {
    return localStorage.getItem(KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

/** The viewer's display currency (undefined = the organisation's default). */
export function useDisplayCurrency(): [string | undefined, (value: string | undefined) => void] {
  const [value, setValue] = useState<string | undefined>(read);
  const set = useCallback((next: string | undefined) => {
    setValue(next);
    try {
      if (next) localStorage.setItem(KEY, next);
      else localStorage.removeItem(KEY);
    } catch {
      // storage unavailable: keep it for this visit only
    }
  }, []);
  return [value, set];
}
