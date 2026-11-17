import { NAV_ITEMS } from "@/app/nav";
import { hi } from "@/locales/hi";
import { LANGUAGES, language, setLanguage, t } from "./i18n";

describe("interface language", () => {
  afterEach(() => setLanguage("en"));

  it("offers English only for now", () => {
    expect(LANGUAGES.map((l) => l.value)).toEqual(["en"]);
  });

  it("is English by default and falls back to English when a text is not translated", () => {
    expect(language()).toBe("en");
    expect(t("Savings")).toBe("Savings");
    setLanguage("hi");
    expect(t("Savings")).toBe("बचत");
    expect(t("Not in the catalogue")).toBe("Not in the catalogue");
    expect(document.documentElement.lang).toBe("hi");
    expect(localStorage.getItem("ccradar-language")).toBe("hi");
  });

  it("translates every navigation label and description into Hindi", () => {
    const names = new Set(["Radar", "Kubernetes"]); // product names stay as they are
    const missing = NAV_ITEMS.flatMap((i) => [i.label, i.description]).filter((s) => !names.has(s) && !hi[s]);
    expect(missing).toEqual([]);
  });

  it("has no empty or untrimmed translations", () => {
    const bad = Object.entries(hi).filter(([, text]) => text.trim() !== text || text.length === 0);
    expect(bad).toEqual([]);
  });
});
