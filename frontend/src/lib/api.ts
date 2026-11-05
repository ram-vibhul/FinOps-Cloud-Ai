/** Minimal typed client for the CostLens API. Cookies carry the session. */

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const text = await response.text();
  const body: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body ? String(body.detail) : response.statusText;
    throw new ApiError(response.status, detail, body);
  }
  return body as T;
}

export interface InstanceMeta {
  product: string;
  vendor: string;
  version: string;
  deployment_mode: "local" | "saas" | "self_hosted";
  environment: string;
  ai_enabled: boolean;
}

export const api = {
  meta: () => apiFetch<InstanceMeta>("/api/v1/meta"),
};
