import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { routes } from "@/app/routes";
import type { Me } from "@/lib/api";
import { SessionProvider } from "@/lib/session";
import { ThemeProvider } from "@/lib/theme";

export function renderRoute(path = "/") {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return {
    router,
    ...render(
      <ThemeProvider>
        <SessionProvider>
          <RouterProvider router={router} />
        </SessionProvider>
      </ThemeProvider>,
    ),
  };
}

export function mockFetchJson(body: unknown, status = 200) {
  // A fresh Response per call: a body can only be read once.
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () =>
      new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
    );
}

type Handler = unknown | ((init: RequestInit | undefined, url: URL) => unknown);
export type Routes = Record<string, Handler>;

export class Reply {
  constructor(
    readonly status: number,
    readonly body: unknown = null,
  ) {}
}

/**
 * Fake API keyed by "METHOD /path" (query string ignored). Handlers return a body,
 * or a ``Reply`` for another status. Unknown routes answer 404 problem+json.
 */
export function mockApi(handlers: Routes) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
    calls.push({ method, path: url.pathname, body });
    const key = `${method} ${url.pathname}`;
    if (!(key in handlers)) {
      return new Response(JSON.stringify({ title: "Not Found", status: 404, code: "not_found" }), { status: 404 });
    }
    const handler = handlers[key];
    let result = typeof handler === "function" ? (handler as (i: RequestInit | undefined, u: URL) => unknown)(init, url) : handler;
    let status = method === "DELETE" || (result === null && method !== "GET") ? 204 : 200;
    if (result instanceof Reply) {
      status = result.status;
      result = result.body;
    }
    return new Response(status === 204 ? null : JSON.stringify(result), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  return { spy, calls };
}

export const ANONYMOUS = new Reply(401, { title: "Unauthorized", status: 401, code: "unauthorized" });

export function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    user: { id: "u1", email: "asha@acme.com", name: "Asha", avatar_url: null },
    organizations: [{ id: "o1", name: "Acme", slug: "acme", role: "owner", is_demo: false }],
    active_organization_id: "o1",
    role: "owner",
    csrf_token: "t",
    ...overrides,
  };
}

export const META = {
  product: "CostLens",
  vendor: "Yugiverse",
  version: "0.1.0",
  deployment_mode: "self_hosted",
  environment: "test",
  ai_enabled: false,
};
