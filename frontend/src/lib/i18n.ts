/** Interface language (11.37). English is the source: each string is its own key, and a
 * missing translation falls back to English, so a partial catalogue is always safe.
 * The choice is kept on this browser, like the number format. */
import { hi } from "@/locales/hi";

export type Language = "en" | "hi";
/** Languages offered to viewers. English only for now (owner decision, 2026-11-13):
 * the Hindi draft stays in the code, unoffered, until a native speaker reviews it. To
 * offer it, add { value: "hi", label: "हिन्दी (Hindi)" } here. */
export const LANGUAGES: { value: Language; label: string; draft?: boolean }[] = [
  { value: "en", label: "English" },
];
const CATALOGUES: Record<Language, Record<string, string>> = { en: {}, hi };
const KEY = "ccradar-language";

function read(): Language {
  try {
    const v = localStorage.getItem(KEY);
    return LANGUAGES.some((l) => l.value === v) ? (v as Language) : "en";
  } catch {
    return "en";
  }
}

let current: Language = read();
if (typeof document !== "undefined") document.documentElement.lang = current;

export function language(): Language {
  return current;
}

/** Remember the language on this browser; pages opened from now on use it. */
export function setLanguage(value: Language): void {
  current = value;
  if (typeof document !== "undefined") document.documentElement.lang = value;
  try {
    if (value === "en") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, value);
  } catch {
    // storage unavailable: keep it for this visit only
  }
}

/** The text in the viewer's language, or the English text when not translated yet. */
export function t(text: string): string {
  return CATALOGUES[current][text] ?? text;
}
