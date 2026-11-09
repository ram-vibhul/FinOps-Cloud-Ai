import { BarChart, LineChart } from "echarts/charts";
import { AriaComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import type { EChartsCoreOption } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { useTheme } from "@/lib/theme";

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, AriaComponent, SVGRenderer]);

export interface ChartColors {
  series: string[];
  other: string;
  grid: string;
  text: string;
  muted: string;
  surface: string;
}

/** Read the palette from CSS variables so light/dark come from one place. */
export function chartColors(): ChartColors {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    series: Array.from({ length: 8 }, (_, i) => v(`--series-${i + 1}`, "#2a78d6")),
    other: v("--series-other", "#9a9994"),
    grid: v("--chart-grid", "#e5e5e5"),
    text: v("--foreground", "#111"),
    muted: v("--muted-foreground", "#666"),
    surface: v("--card", "#fff"),
  };
}

export function EChart({
  build,
  height = 280,
  label,
}: {
  build: (colors: ChartColors) => EChartsCoreOption;
  height?: number;
  label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { resolved } = useTheme();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = echarts.init(el, undefined, { renderer: "svg" });
    chart.setOption({ aria: { enabled: true, label: { description: label } }, ...build(chartColors()) }, true);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
    // `resolved` re-reads the palette from CSS when the theme changes.
    // oxlint-disable-next-line react/exhaustive-effect-dependencies -- intentional trigger
  }, [build, label, resolved]);

  return (
    <figure aria-label={label} className="m-0 w-full">
      <div ref={ref} style={{ height }} />
    </figure>
  );
}
