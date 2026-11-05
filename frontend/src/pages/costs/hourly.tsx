import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { StackedSpendChart } from "./charts";

/** Cost per hour for the last 72 hours; shown only when hourly data exists (CUR). */
export function HourlyCard() {
  const me = useMe();
  const { data } = useLoad(() => api.hourly(72), [me.active_organization_id]);
  if (!data || data.series.length === 0) return null;
  const total = data.series.reduce((a, s) => a + s.total, 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Last 72 hours</CardTitle>
        <CardDescription>Hourly cost by service, from the hourly billing export.</CardDescription>
      </CardHeader>
      <CardContent>
        <StackedSpendChart data={{
          currency: data.currency, total,
          periods: data.hours.map((h) => h.slice(0, 13).replace("T", " ") + ":00"),
          series: data.series,
        }} />
      </CardContent>
    </Card>
  );
}
