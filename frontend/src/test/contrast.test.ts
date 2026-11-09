import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Colour contrast of the theme (9.3), light and dark, computed from the CSS tokens:
 * WCAG AA needs 4.5:1 for normal text.
 */
const css = readFileSync(resolve(__dirname, "../index.css"), "utf8");

function block(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  const body = css.slice(start, css.indexOf("}", start));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) out[m[1]!] = m[2]!.trim();
  return out;
}

const clamp = (v: number) => Math.min(1, Math.max(0, v));

function oklchToLinear(l: number, c: number, h: number): [number, number, number] {
  const a = c * Math.cos((h * Math.PI) / 180);
  const b = c * Math.sin((h * Math.PI) / 180);
  const lc = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const mc = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const sc = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    clamp(4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc),
    clamp(-1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc),
    clamp(-0.0041960863 * lc - 0.7034186147 * mc + 1.707614701 * sc),
  ];
}

function luminance(tokens: Record<string, string>, name: string): number {
  let v = tokens[name]!;
  while (v.startsWith("var(")) v = tokens[v.slice(6, -1)]!;
  const m = v.match(/oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)/);
  if (!m) throw new Error(`not an oklch colour: ${name} = ${v}`);
  const [r, g, b] = oklchToLinear(Number(m[1]), Number(m[2]), Number(m[3]));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b; // already linear light
}

function ratio(tokens: Record<string, string>, fg: string, bg: string): number {
  const [a, b] = [luminance(tokens, fg), luminance(tokens, bg)].toSorted((x, y) => y - x);
  return (a! + 0.05) / (b! + 0.05);
}

const PAIRS: [string, string][] = [
  ["foreground", "background"],
  ["foreground", "card"],
  ["muted-foreground", "background"],
  ["muted-foreground", "card"],
  ["primary-foreground", "primary"],
  ["primary", "background"],
  ["destructive", "background"],
];

describe.each([[":root"], [".dark"]])("contrast in %s", (selector) => {
  const tokens = block(selector);
  it.each(PAIRS)("%s on %s is at least 4.5:1", (fg, bg) => {
    expect(ratio(tokens, fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
});
