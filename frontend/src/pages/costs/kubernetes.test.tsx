import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

vi.mock("@/components/chart/echart", () => ({
  EChart: ({ label }: { label: string }) => <figure aria-label={label} />,
  chartColors: () => ({}),
}));

const BASE = {
  currency: "USD", total: 1000, allocated: 640, idle: 360, idle_pct: 36, estimated_pct: 100, hours: 720,
  daily: [{ day: "2026-11-03", allocated: 21, idle: 12 }, { day: "2026-11-04", allocated: 22, idle: 11 }],
  clusters: [{ id: "k1", name: "prod-ap-south-1" }],
};
const NAMESPACES = { ...BASE, items: [
  { key: "checkout", label: "checkout", cost: 300, share_pct: 30, cpu: 7.5, memory_gib: 15, estimated: true },
  { key: "Idle", label: "Idle capacity", cost: 360, share_pct: 36, cpu: 20, memory_gib: 90, estimated: true, idle: true },
] };
const WORKLOADS = { ...BASE, items: [
  { key: "checkout/Deployment/checkout-api", label: "checkout-api", kind: "Deployment", namespace: "checkout",
    cost: 240, share_pct: 24, cpu: 6, memory_gib: 12, estimated: true },
] };

describe("kubernetes costs", () => {
  it("shows totals, idle share and drills from namespace to workloads", async () => {
    const queries: string[] = [];
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/k8s/investigations": [{ id: "i1", title: "Deployment api: Most likely: crash", trigger: "auto", status: "open",
        cluster: "prod", created_at: "2026-11-05T10:00:00Z" }],
      "GET /api/v1/k8s/rightsizing": { currency: "USD", headroom_pct: 20, days: 14, monthly_saving: 0, items: [] },
      "GET /api/v1/k8s/costs": (_init: RequestInit | undefined, url: URL) => {
        queries.push(url.search);
        return url.searchParams.get("group_by") === "workload" ? WORKLOADS : NAMESPACES;
      },
    });
    renderRoute("/kubernetes");
    expect(await screen.findByText("(36%)")).toBeInTheDocument();
    expect(await screen.findByText("Deployment api: Most likely: crash")).toHaveAttribute("href", "/kubernetes/investigations/i1");
    expect(screen.getByText(/100% of this is estimated/)).toBeInTheDocument();
    expect(screen.getByRole("figure", { name: /Daily spend, total \$1,000/ })).toBeInTheDocument();
    expect(screen.getByText("Idle capacity", { selector: "span" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "checkout" }));
    expect(await screen.findByText("checkout-api")).toBeInTheDocument();
    await waitFor(() => expect(queries.some((q) => q.includes("group_by=workload") && q.includes("namespace=checkout"))).toBe(true));
  });

  it("shows right-sizing advice with peaks, OOM risk and savings", async () => {
    const item = { cluster: "prod", cluster_id: "k1", namespace: "payments", kind: "Deployment", workload: "payments-gateway",
      container: "payments-gateway", replicas: 4, hours: 336, cpu_request: 2, memory_request_gib: 4, memory_limit_gib: null,
      cpu_avg: 0.12, cpu_peak: 0.28, memory_avg_gib: 0.6, memory_peak_gib: 0.85, workload_monthly_cost: 230,
      monthly_saving: 190.5, action: "decrease", oom_risk: false, recommended_cpu: 0.34, recommended_memory_gib: 1.0313,
      recommended_memory_limit_gib: null };
    const etl = { ...item, namespace: "data", kind: "CronJob", workload: "nightly-etl", container: "etl", replicas: 1,
      cpu_request: 1, cpu_peak: 0.95, memory_request_gib: 3, memory_limit_gib: 3, memory_peak_gib: 2.95, action: "ok", oom_risk: true,
      monthly_saving: 0, recommended_cpu: 1, recommended_memory_gib: 3, recommended_memory_limit_gib: 3.5469 };
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/k8s/costs": NAMESPACES,
      "GET /api/v1/k8s/investigations": [],
      "GET /api/v1/k8s/rightsizing": { currency: "USD", headroom_pct: 20, days: 14, monthly_saving: 190.5, items: [etl, item] },
    });
    renderRoute("/kubernetes");
    expect(await screen.findByText("Right-sizing")).toBeInTheDocument();
    expect(screen.getByText(/could free about/)).toHaveTextContent("$191 a month");
    expect(screen.getByText("Lower requests")).toBeInTheDocument();
    expect(screen.getByText("340m")).toBeInTheDocument();
    expect(screen.getByText("peak 280m")).toBeInTheDocument();
    expect(screen.getByText(/near the 3Gi limit/)).toHaveTextContent("set it to at least 3.55Gi");
    expect(screen.getByText("Raise memory limit")).toBeInTheDocument();
    expect(screen.getByText("−$191")).toBeInTheDocument();
  });
});
