import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { numberLocale } from "@/lib/format";
import { useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

function tonnes(kg: number): string {
  const t = kg / 1000;
  return `${t.toLocaleString(numberLocale(), { maximumFractionDigits: t < 10 ? 2 : 1 })} t`;
}

/** Carbon (11.26): estimated emissions of cloud use, from the bill. */
export function CarbonCard() {
  const me = useMe();
  const data = useLoad(() => api.carbon(6), [me.active_organization_id]).data;
  if (!data || data.total_kg <= 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Carbon, last 6 months</CardTitle>
        <CardDescription>
          About {tonnes(data.total_kg)} CO2e.{" "}
          {data.measured_share !== null && `${data.measured_share}% worked out from what was used, the rest from spend. `}
          {data.method} An estimate: your cloud providers' own carbon tools are the reference for official reporting.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        <Table aria-label="Carbon by month">
          <THead><TR><TH>Month</TH><TH className="text-right">From use</TH><TH className="text-right">From spend</TH></TR></THead>
          <TBody>
            {data.months.map((m) => (
              <TR key={m.month}><TD>{m.month}</TD><TD className="text-right tabular-nums">{tonnes(m.from_usage)}</TD>
                <TD className="text-right tabular-nums">{tonnes(m.from_spend)}</TD></TR>
            ))}
          </TBody>
        </Table>
        <Table aria-label="Carbon by region">
          <THead><TR><TH>Region</TH><TH className="text-right">CO2e</TH></TR></THead>
          <TBody>
            {data.by_region.slice(0, 6).map((r) => (
              <TR key={r.name}><TD>{r.name}</TD><TD className="text-right tabular-nums">{tonnes(r.kg)}</TD></TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}
