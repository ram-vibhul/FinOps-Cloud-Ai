import { money, setNumberLocale } from "./format";

describe("number formats (11.37)", () => {
  afterEach(() => setNumberLocale(""));

  it("groups in lakhs and crores for India", () => {
    setNumberLocale("en-IN");
    expect(money(1234567, "INR")).toBe("₹12,34,567");
    expect(money(12345678, "INR", { compact: true })).toMatch(/^₹1\.2\s?Cr$/);
    expect(localStorage.getItem("ccradar-number-format")).toBe("en-IN");
  });

  it("uses international grouping and forgets the choice", () => {
    setNumberLocale("en-US");
    expect(money(1234567, "USD")).toBe("$1,234,567");
    setNumberLocale("");
    expect(localStorage.getItem("ccradar-number-format")).toBeNull();
  });
});
