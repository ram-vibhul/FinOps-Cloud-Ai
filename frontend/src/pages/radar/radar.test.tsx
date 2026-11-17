import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeMe, mockApi, renderRoute } from "@/test/render";

const FEED = {
  currency: "USD", hours: 24, billed_through: "2026-11-09", added_monthly: 2277.97, removed_monthly: -248.2,
  net_monthly: 2029.77, not_in_bill_monthly: 2245.12, unpriced: 2,
  accuracy: { measured: 12, median_error_pct: 8.5, within_pct: 25, share_within: 91.7 },
  items: [
    { id: "1", occurred_at: new Date().toISOString(), title: "RunInstances", actor: "ravi", account: "222222222222", region: "ap-south-1",
      service: "Amazon Elastic Compute Cloud", monthly: 2245.12, label: "4 x m5.4xlarge", source: "bill", in_bill: false,
      undo: { text: "Terminate the instance if it is not needed.", command: "aws ec2 terminate-instances --region ap-south-1 --instance-ids i-0abc",
        console: "https://ap-south-1.console.aws.amazon.com/ec2/home?region=ap-south-1" },
      measured: { monthly: 590, estimate: 561.28, error_pct: 5.1, days: 7, through: "2026-11-08" } },
    { id: "2", occurred_at: "2026-11-09T08:00:00Z", title: "TerminateInstances", actor: "platform-bot", account: null, region: "us-east-1",
      service: null, monthly: -248.2, label: "terminated 1 x c5.2xlarge", source: "list price", in_bill: true },
  ],
};

describe("live radar", () => {
  it("shows what each change costs and what is not in the bill yet", async () => {
    const { calls } = mockApi({ "GET /api/v1/auth/me": makeMe(), "GET /api/v1/radar": FEED });
    renderRoute("/radar");
    expect(await screen.findByText("4 x m5.4xlarge")).toBeInTheDocument();
    const row = screen.getByText("4 x m5.4xlarge").closest("tr") as HTMLElement;
    expect(within(row).getByText("ravi")).toBeInTheDocument();
    expect(within(row).getByText("+$2,245")).toBeInTheDocument();
    expect(within(row).getByText("Not in the bill yet")).toBeInTheDocument();
    expect(within(row).getByText(/priced from your own rates/)).toBeInTheDocument();
    const saved = screen.getByText("terminated 1 x c5.2xlarge").closest("tr") as HTMLElement;
    expect(within(saved).getByText("−$248")).toBeInTheDocument();
    expect(within(saved).getByText("In the bill")).toBeInTheDocument();
    expect(screen.getByText(/2 other changes could not be priced/)).toBeInTheDocument();
    expect(screen.getByText(/91.7% of 12 estimates were within 25%/)).toBeInTheDocument();
    expect(within(row).getByText(/Measured in the bill: \$590 a month per resource over 7 days \(estimate under by 5.1%\)/)).toBeInTheDocument();
    await userEvent.click(within(row).getByText("How to undo"));
    expect(within(row).getByText(/terminate-instances --region ap-south-1/)).toBeVisible();
    expect(within(row).getByRole("link", { name: "Open in the AWS console" })).toHaveAttribute("rel", "noreferrer");
    expect(within(saved).queryByText("How to undo")).not.toBeInTheDocument();
    expect(screen.getByText(/Changes made on or after 2026-11-09/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Show"), "168");
    await waitFor(() => expect(calls.filter((c) => c.path === "/api/v1/radar").length).toBeGreaterThanOrEqual(2));
    expect(screen.getByRole("link", { name: "Radar" })).toHaveAttribute("href", "/radar");
  });

  it("says when a scan was cut short", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/radar": { ...FEED, scans: [
        { connection: "Prod", at: "2026-11-12T06:00:00Z", complete: false, problems: ["us-east-1: more than 2000 changes"] },
        { connection: "Dev", at: "2026-11-12T06:00:00Z", complete: true, problems: [] },
      ] },
    });
    renderRoute("/radar");
    const note = await screen.findByRole("note");
    expect(note).toHaveTextContent("Some changes in Prod may not be shown yet");
    expect(note).toHaveTextContent("more than 2000 changes");
    expect(screen.queryByText(/Some changes in Dev/)).not.toBeInTheDocument();
  });

  it("asks to confirm when a chat button marks a change as expected", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/radar": FEED,
      "PUT /api/v1/radar/1/expected": { id: "1", expected: { by: "Owner", at: "2026-11-12T06:00:00Z", note: null } },
    });
    renderRoute("/radar?change=1&expected=1");
    const ask = await screen.findByRole("alertdialog", { name: "Mark as expected" });
    expect(ask).toHaveTextContent("4 x m5.4xlarge");
    expect(screen.getAllByRole("row").find((r) => r.getAttribute("aria-current"))).toHaveTextContent("ravi");
    await userEvent.click(within(ask).getByRole("button", { name: "Mark as expected" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.path === "/api/v1/radar/1/expected")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ expected: true });
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("explains itself when nothing has changed", async () => {
    mockApi({ "GET /api/v1/auth/me": makeMe(), "GET /api/v1/radar": { ...FEED, items: [], unpriced: 0 } });
    renderRoute("/radar");
    expect(await screen.findByText("No costly changes in this period")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect an AWS account" })).toHaveAttribute("href", "/settings?tab=clouds");
  });
});
