import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockFetchJson, renderRoute } from "@/test/render";

const META = {
  product: "CostLens",
  vendor: "Yugiverse",
  version: "0.1.0",
  deployment_mode: "self_hosted",
  environment: "test",
  ai_enabled: false,
};

describe("app shell", () => {
  it("renders the overview with instance details", async () => {
    mockFetchJson(META);
    renderRoute("/");
    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(await screen.findByText(/CostLens 0.1.0 · self-hosted · test/)).toBeInTheDocument();
  });

  it("shows an error when the API is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));
    renderRoute("/");
    expect(await screen.findByText("Could not reach the API.")).toBeInTheDocument();
  });

  it("navigates between sections", async () => {
    mockFetchJson(META);
    renderRoute("/");
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0]!;
    await userEvent.click(nav.querySelector('a[href="/savings"]')!);
    expect(screen.getByRole("heading", { level: 1, name: "Savings" })).toBeInTheDocument();
  });

  it("shows not found for unknown paths", () => {
    renderRoute("/nope");
    expect(screen.getByText("Page not found")).toBeInTheDocument();
  });

  it("opens and closes mobile navigation", async () => {
    mockFetchJson(META);
    renderRoute("/");
    await userEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(screen.getByRole("dialog", { name: "Navigation" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close navigation" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
