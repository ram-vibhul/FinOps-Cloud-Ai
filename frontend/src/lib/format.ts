/** Money and percentage formatting for charts, tiles and tables. */

/** Number formats a viewer can choose (11.37); "" follows the browser. en-IN groups in
 * lakhs and crores (₹12,34,567; compact ₹12.3L, ₹1.2Cr). */
export const NUMBER_FORMATS: { value: string; label: string; example: string }[] = [
  { value: "", label: "Same as this browser", example: "" },
  { value: "en-IN", label: "India (lakh, crore)", example: "12,34,567" },
  { value: "en-US", label: "International", example: "1,234,567" },
  { value: "en-GB", label: "United Kingdom", example: "1,234,567" },
  { value: "de-DE", label: "Europe (1.234.567,89)", example: "1.234.567" },
];
const LOCALE_KEY = "ccradar-number-format";

function readLocale(): string | undefined {
  try {
    return localStorage.getItem(LOCALE_KEY) || undefined;
  } catch {
    return undefined;
  }
}

let locale: string | undefined = readLocale();

export function numberLocale(): string | undefined {
  return locale;
}

/** Remember the viewer's number format on this browser and use it from now on. */
export function setNumberLocale(value: string): void {
  locale = value || undefined;
  try {
    if (value) localStorage.setItem(LOCALE_KEY, value);
    else localStorage.removeItem(LOCALE_KEY);
  } catch {
    // storage unavailable: keep it for this visit only
  }
}

export function money(value: number, currency: string, opts: { compact?: boolean } = {}): string {
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      notation: opts.compact ? "compact" : "standard",
      maximumFractionDigits: opts.compact ? 1 : Math.abs(value) < 100 ? 2 : 0,
    }).format(value);
  } catch {
    return `${value.toFixed(2)} ${currency}`;
  }
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export function shortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString(locale, { day: "numeric", month: "short" });
}

export function monthLabel(isoMonth: string): string {
  const d = new Date(`${isoMonth}-01T00:00:00`);
  return d.toLocaleDateString(locale, { month: "short", year: "2-digit" });
}
