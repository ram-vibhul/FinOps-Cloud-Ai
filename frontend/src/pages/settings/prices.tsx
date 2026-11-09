import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, type ClusterPrices, errorMessage, type K8sCluster } from "@/lib/api";
import { CURRENCIES } from "@/lib/currency";

const LIST: ClusterPrices = { currency: "USD", cpu_hour: 0.031611, gib_hour: 0.004237, node_hour: 0, spot_discount_pct: 0, always: false };

export function PricesPanel({ cluster, onSaved }: { cluster: K8sCluster; onSaved: () => void }) {
  const [p, setP] = useState<ClusterPrices>(cluster.prices ?? LIST);
  const id = (name: string) => `${name}-${cluster.id}`;
  const num = (field: keyof ClusterPrices) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setP({ ...p, [field]: Number(e.target.value) });

  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      await api.setClusterPrices(cluster.id, p);
      toast.success("Saved. New prices apply from the next hourly allocation.");
      onSaved();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function reset() {
    try {
      await api.clearClusterPrices(cluster.id);
      toast.success("Back to the bill and list prices.");
      onSaved();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <form onSubmit={(e) => void save(e)} className="basis-full grid gap-3 rounded-md border p-3 text-sm">
      <p className="text-muted-foreground">
        For clusters outside AWS (on-premises, other clouds) or negotiated rates. Without your prices, nodes use the AWS bill
        when it has them, otherwise public list prices.
      </p>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <div className="grid gap-1">
          <Label htmlFor={id("pc-currency")}>Currency</Label>
          <NativeSelect id={id("pc-currency")} value={p.currency} onChange={(e) => setP({ ...p, currency: e.target.value })}>
            {CURRENCIES.map((c) => <option key={c}>{c}</option>)}
          </NativeSelect>
        </div>
        <div className="grid gap-1">
          <Label htmlFor={id("pc-cpu")}>Per vCPU-hour</Label>
          <Input id={id("pc-cpu")} type="number" step="any" min={0} value={p.cpu_hour} onChange={num("cpu_hour")} required />
        </div>
        <div className="grid gap-1">
          <Label htmlFor={id("pc-gib")}>Per GiB-hour</Label>
          <Input id={id("pc-gib")} type="number" step="any" min={0} value={p.gib_hour} onChange={num("gib_hour")} required />
        </div>
        <div className="grid gap-1">
          <Label htmlFor={id("pc-node")}>Per node-hour</Label>
          <Input id={id("pc-node")} type="number" step="any" min={0} value={p.node_hour} onChange={num("node_hour")} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor={id("pc-spot")}>Spot discount %</Label>
          <Input id={id("pc-spot")} type="number" min={0} max={95} value={p.spot_discount_pct} onChange={num("spot_discount_pct")} />
        </div>
      </div>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={p.always} onChange={(e) => setP({ ...p, always: e.target.checked })} />
        Always use these prices, even where the AWS bill has the node
      </label>
      <div className="flex gap-2">
        <Button type="submit" size="sm">Save prices</Button>
        {cluster.prices && <Button type="button" size="sm" variant="ghost" onClick={() => void reset()}>Use list prices</Button>}
      </div>
    </form>
  );
}
