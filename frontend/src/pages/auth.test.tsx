import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ANONYMOUS, META, Reply, makeMe, mockApi, renderRoute } from "@/test/render";

describe("sign-in page", () => {
  it("lists providers with the return path and explains errors", async () => {
    mockApi({
      "GET /api/v1/auth/me": ANONYMOUS,
      "GET /api/v1/auth/providers": [{ id: "google", name: "Google" }],
    });
    renderRoute("/sign-in?return_to=%2Fcosts&error=email_not_verified");
    const link = await screen.findByRole("link", { name: /Continue with Google/ });
    expect(link).toHaveAttribute("href", "/api/v1/auth/login/google?return_to=%2Fcosts");
    expect(screen.getByRole("alert")).toHaveTextContent(/has not confirmed that you own this email/);
  });

  it("finds company single sign-on by organisation", async () => {
    mockApi({
      "GET /api/v1/auth/me": ANONYMOUS,
      "GET /api/v1/auth/providers": [],
      "GET /api/v1/auth/organizations/contoso/providers": [{ id: "org:1", name: "Contoso SSO" }],
    });
    renderRoute("/sign-in");
    expect(await screen.findByText(/No sign-in provider is configured/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Company single sign-on"), "Contoso");
    await userEvent.click(screen.getByRole("button", { name: /Find/ }));
    expect(await screen.findByRole("link", { name: "Continue with Contoso SSO" })).toHaveAttribute(
      "href",
      "/api/v1/auth/login/org%3A1",
    );
  });

  it("signs in with a local development account", async () => {
    let signedIn = false;
    const { calls } = mockApi({
      "GET /api/v1/auth/me": () => (signedIn ? makeMe() : ANONYMOUS),
      "GET /api/v1/auth/providers": [{ id: "local", name: "Local account (development)" }],
      "POST /api/v1/auth/local": () => {
        signedIn = true;
        return null;
      },
      "GET /api/v1/meta": META,
    });
    const { router } = renderRoute("/sign-in");
    await userEvent.type(await screen.findByLabelText(/Local account/), "dev@x.com");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(calls.find((c) => c.path === "/api/v1/auth/local")?.body).toEqual({ email: "dev@x.com", name: "" });
  });

  it("redirects signed-in users away", async () => {
    mockApi({ "GET /api/v1/auth/me": makeMe(), "GET /api/v1/meta": META });
    const { router } = renderRoute("/sign-in?return_to=%2Fsettings");
    await waitFor(() => expect(router.state.location.pathname).toBe("/settings"));
  });
});

describe("invitation page", () => {
  const preview = { organization: "Acme", role: "member", email_hint: "ne*@x.com", invited_by: "Asha", status: "pending" };

  it("asks anonymous visitors to sign in first", async () => {
    mockApi({ "GET /api/v1/auth/me": ANONYMOUS, "GET /api/v1/invitations/tok": preview });
    renderRoute("/invite/tok");
    expect(await screen.findByRole("heading", { name: "Join Acme" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in to accept" })).toHaveAttribute(
      "href",
      "/sign-in?return_to=%2Finvite%2Ftok",
    );
  });

  it("accepts and goes to the organisation", async () => {
    let joined = false;
    mockApi({
      "GET /api/v1/auth/me": () => (joined ? makeMe() : makeMe({ organizations: [], active_organization_id: null })),
      "GET /api/v1/invitations/tok": preview,
      "POST /api/v1/invitations/tok/accept": () => {
        joined = true;
        return null;
      },
      "GET /api/v1/meta": META,
    });
    const { router } = renderRoute("/invite/tok");
    await userEvent.click(await screen.findByRole("button", { name: "Accept invitation" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("explains a wrong-account error", async () => {
    mockApi({
      "GET /api/v1/auth/me": makeMe(),
      "GET /api/v1/invitations/tok": preview,
      "POST /api/v1/invitations/tok/accept": new Reply(403, {
        title: "Forbidden", status: 403, code: "wrong_account",
        detail: "This invitation is for a different email address. Sign in as ne*@x.com.",
      }),
    });
    renderRoute("/invite/tok");
    await userEvent.click(await screen.findByRole("button", { name: "Accept invitation" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/different email address/);
  });

  it("handles unknown links", async () => {
    mockApi({ "GET /api/v1/auth/me": ANONYMOUS });
    renderRoute("/invite/bad");
    expect(await screen.findByRole("heading", { name: "Invitation not found" })).toBeInTheDocument();
  });
});

describe("welcome page", () => {
  it("creates an organisation", async () => {
    let created = false;
    const { calls } = mockApi({
      "GET /api/v1/auth/me": () =>
        created ? makeMe() : makeMe({ organizations: [], active_organization_id: null, role: null }),
      "POST /api/v1/organizations": () => {
        created = true;
        return { id: "o1", name: "Acme", slug: "acme", is_demo: false, default_currency: "USD", created_at: "" };
      },
      "GET /api/v1/meta": META,
    });
    const { router } = renderRoute("/welcome");
    await userEvent.type(await screen.findByLabelText("Organisation name"), "Acme");
    await userEvent.click(screen.getByRole("button", { name: /Create organisation/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(calls.find((c) => c.path === "/api/v1/organizations")?.body).toEqual({ name: "Acme" });
  });

  it("joins the demo", async () => {
    const { calls } = mockApi({
      "GET /api/v1/auth/me": makeMe({ organizations: [], active_organization_id: null, role: null }),
      "POST /api/v1/demo/join": { organization_id: "d" },
    });
    renderRoute("/welcome");
    await userEvent.click(await screen.findByRole("button", { name: /Explore the demo/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/demo/join")).toBe(true));
  });
});
