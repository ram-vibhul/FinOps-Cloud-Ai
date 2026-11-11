import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

vi.mock("@/components/chart/echart", () => ({
  EChart: ({ label }: { label: string }) => <figure aria-label={label} />,
  chartColors: () => ({}),
}));

const RESULT = {
  target: "CronJob nightly-etl",
  summary: "Most likely: out of memory: the container uses more memory than its limit (95% confidence).",
  narrative: "In the last 6 hours, 1 of 1 pod(s) of CronJob nightly-etl were running.",
  status_update: "[Investigating] CronJob nightly-etl (prod / data)\nLikely cause: Out of memory (95% confidence).",
  worded_by: "ai",
  correlation: {
    currency: "USD", anchor: { at: "2026-11-04T04:00:00Z", title: "acme/nightly-etl: Deploy to production", source: "github", url: null },
    first_warning: "2026-11-04T21:00:00Z",
    effects: [{ signal: "memory", label: "memory per replica", before: 1.2, after: 2.2, change_pct: 83, text: "memory per replica up 83%" }],
    story: ["4 Nov 04:00 — change: acme/nightly-etl: Deploy to production", "→ memory per replica up 83% (1.20 GiB → 2.20 GiB)"],
    series: { hours: ["2026-11-04T03:00:00Z", "2026-11-04T04:00:00Z", "2026-11-04T05:00:00Z"], cost: [1, 1, 1], cpu: [0.5, 0.6, 0.6], memory: [1.2, 2.1, 2.2], warnings: [null, null, 3] },
    markers: [{ at: "2026-11-04T04:00:00Z", label: "Deploy", type: "change" }], cost_anomalies: [],
  },
  causes: [{ check: "oom", title: "Out of memory: the container uses more memory than its limit", confidence: 0.95,
    summary: "CronJob nightly-etl is killed by the kernel.", evidence: ["1 container(s) last ended with OOMKilled (exit 137); 14 restarts in total"],
    fixes: ["Raise the memory limit to at least 3.90 GiB (and the request to match peak use)."], verify: ["No OOMKilled restarts for 24 hours."] }],
  facts: { pods: 1, running: 1, restarts: 14, warning_events: 2, nodes: ["node-a"], changes: [], logs: "Fetch logs on demand.",
    pod_list: [{ name: "nightly-etl-0", namespace: "data", containers: ["etl"], restarts: 14 }] },
  timeline: [{ at: "2026-11-04T23:00:00+00:00", type: "Warning", reason: "BackOff", object: "Pod/etl-0", message: "Back-off restarting", count: 14 }],
};

const ACCURACY = {
  closed: 4, top1_pct: 50, top3_pct: 75, missed: 1,
  checks: [{ check: "oom", label: "Out of memory", ranked_first: 1, real: 2, precision_pct: 100, recall_pct: 50 }],
  by_month: [], lab: { accuracy: 1, correct: 12, scenarios: 12, kind: "replay" },
  causes: [{ check: "oom", label: "Out of memory" }, { check: "crash_loop", label: "Crash on start-up (application error)" },
    { check: "other", label: "Something else" }],
};

describe("investigate", () => {
  it("lists workloads by health, investigates and saves", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "member" }),
      "GET /api/v1/clusters": [{ id: "k1", name: "prod", health: "ok", objects: {} }],
      "GET /api/v1/k8s/workloads": [
        { namespace: "data", kind: "CronJob", name: "nightly-etl", pods: 1, running: 1, restarts: 14, reasons: ["CrashLoopBackOff", "OOMKilled"], health: "failing" },
        { namespace: "web", kind: "Deployment", name: "storefront", pods: 4, running: 4, restarts: 0, reasons: [], health: "ok" },
      ],
      "GET /api/v1/k8s/investigations": [],
      "GET /api/v1/k8s/accuracy": ACCURACY,
      "POST /api/v1/k8s/investigations/run": RESULT,
      "POST /api/v1/k8s/investigations": { id: "i1" },
    });
    renderRoute("/kubernetes/investigate");
    expect(await screen.findByText("Failing")).toBeInTheDocument();
    expect(await screen.findByText("Diagnosis accuracy")).toBeInTheDocument();
    expect(screen.getByText("12/12")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Investigate nightly-etl" }));
    expect(await screen.findByText("95% likely")).toBeInTheDocument();
    expect(screen.getByText(/Raise the memory limit to at least 3.90 GiB/)).toBeInTheDocument();
    expect(screen.getByText("No OOMKilled restarts for 24 hours.")).toBeInTheDocument();
    expect(screen.getByText("BackOff")).toBeInTheDocument();
    expect(screen.getByText(/In the last 6 hours, 1 of 1 pod/)).toBeInTheDocument();
    expect(screen.getByText("Cause and effect")).toBeInTheDocument();
    expect(screen.getByText("→ memory per replica up 83% (1.20 GiB → 2.20 GiB)")).toBeInTheDocument();
    expect(screen.getByText("+83%")).toBeInTheDocument();
    expect(screen.getAllByLabelText(/per hour, with change and first-warning markers/)).toHaveLength(3);
    expect(screen.getByText(/Worded by AI/)).toBeInTheDocument();
    expect(screen.getByText(/\[Investigating\] CronJob nightly-etl/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/v1/k8s/investigations/run")?.body).toEqual({
      cluster_id: "k1", namespace: "data", kind: "CronJob", name: "nightly-etl", hours: 6 });
    await userEvent.click(screen.getByRole("button", { name: "Save investigation" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/k8s/investigations")).toBe(true));
  });

  it("fetches logs from before the last crash through the agent", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let polls = 0;
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "member" }),
      "GET /api/v1/clusters": [{ id: "k1", name: "prod", health: "ok", objects: {} }],
      "GET /api/v1/k8s/workloads": [{ namespace: "data", kind: "CronJob", name: "nightly-etl", pods: 1, running: 1, restarts: 14, reasons: [], health: "failing" }],
      "GET /api/v1/k8s/investigations": [],
      "POST /api/v1/k8s/investigations/run": RESULT,
      "POST /api/v1/k8s/clusters/k1/tasks": { id: "t1", kind: "logs", params: {}, status: "queued", result: null, error: null, created_at: "", finished_at: null },
      "GET /api/v1/k8s/tasks/t1": () => (++polls < 2
        ? { id: "t1", kind: "logs", params: {}, status: "running", result: null, error: null, created_at: "", finished_at: null }
        : { id: "t1", kind: "logs", params: {}, status: "done", result: "MemoryError: unable to allocate", error: null, created_at: "", finished_at: "" }),
    });
    renderRoute("/kubernetes/investigate");
    await userEvent.click(await screen.findByRole("button", { name: "Investigate nightly-etl" }));
    await userEvent.click(await screen.findByRole("button", { name: "before crash" }));
    expect(calls.find((c) => c.path === "/api/v1/k8s/clusters/k1/tasks")?.body).toEqual({
      kind: "logs", params: { namespace: "data", pod: "nightly-etl-0", container: "etl", previous: true, tail_lines: 200 } });
    expect(await screen.findByText("MemoryError: unable to allocate", {}, { timeout: 8000 })).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("checks whether a fix worked", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "member" }),
      "GET /api/v1/clusters": [{ id: "k1", name: "prod", health: "ok", objects: {} }],
      "GET /api/v1/k8s/workloads": [{ namespace: "data", kind: "CronJob", name: "nightly-etl", pods: 1, running: 1, restarts: 14, reasons: [], health: "failing" }],
      "GET /api/v1/k8s/investigations": [],
      "POST /api/v1/k8s/investigations/run": RESULT,
      "POST /api/v1/k8s/fix-check": {
        verdict: "not_fixed", text: "Not fixed: the workload is still failing with 2.0 failures per hour after the fix.",
        health_now: "failing", fix_at: "2026-11-04T04:00:00Z", incident_start: "2026-11-04T21:00:00Z",
        periods: [
          { period: "before", start: "", end: "", failures_per_hour: 0, failures: 0, cpu: 0.4, memory_gib: 1.2, cost_per_hour: 0.05 },
          { period: "during", start: "", end: "", failures_per_hour: 3, failures: 9, cpu: 0.5, memory_gib: 2.1, cost_per_hour: 0.05 },
          { period: "after", start: "", end: "", failures_per_hour: 2, failures: 6, cpu: 0.5, memory_gib: 2.2, cost_per_hour: 0.05 },
        ],
        side_effects: ["memory per replica is 83% higher than before the incident"],
      },
    });
    renderRoute("/kubernetes/investigate");
    await userEvent.click(await screen.findByRole("button", { name: "Investigate nightly-etl" }));
    await userEvent.click(await screen.findByRole("button", { name: "Check" }));
    expect(await screen.findByText("Not fixed")).toBeInTheDocument();
    expect(screen.getByText("After the fix")).toBeInTheDocument();
    expect(screen.getByText(/Side effect: memory per replica is 83% higher/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/v1/k8s/fix-check")?.body).toEqual({
      cluster_id: "k1", namespace: "data", kind: "CronJob", name: "nightly-etl", fix_at: "2026-11-04T04:00:00Z" });
  });

  it("closes a saved investigation with the real cause", async () => {
    const saved = { id: "i9", cluster_id: "k1", cluster: "prod", namespace: "data", kind: "CronJob", name: "nightly-etl",
      title: "t", status: "open", trigger: "auto", window_start: "2026-11-04T00:00:00Z", window_end: "2026-11-04T06:00:00Z",
      created_at: "2026-11-04T06:00:00Z", result: RESULT, real_cause: null };
    let closed = false;
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ role: "member" }),
      "GET /api/v1/k8s/accuracy": ACCURACY,
      "GET /api/v1/k8s/investigations/i9": () => (closed ? { ...saved, status: "closed", real_cause: "oom", cause_rank: 1, fix_note: "limit 4Gi" } : saved),
      "POST /api/v1/k8s/investigations/i9/close": () => { closed = true; return saved; },
    });
    renderRoute("/kubernetes/investigations/i9");
    await userEvent.type(await screen.findByLabelText("What fixed it (optional)"), "limit 4Gi");
    await userEvent.click(screen.getByRole("button", { name: "Close investigation" }));
    expect(await screen.findByText("CloudCostRadar ranked it first")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/v1/k8s/investigations/i9/close")?.body).toEqual({
      real_cause: "oom", fix_note: "limit 4Gi" });
  });
});
