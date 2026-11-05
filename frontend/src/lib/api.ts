/** Minimal typed client for the CostLens API. Cookies carry the session. */

/** RFC 9457 problem details, as returned by the API for every error. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  code: string;
  detail?: string;
  request_id?: string;
  errors?: { loc: (string | number)[]; msg: string; type: string }[];
}

function isProblem(body: unknown): body is Problem {
  return !!body && typeof body === "object" && "status" in body && "title" in body;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | undefined;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.code = isProblem(body) ? body.code : "http_error";
    this.requestId = isProblem(body) ? body.request_id : undefined;
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    const message = isProblem(body) ? (body.detail ?? body.title) : response.statusText || "Request failed";
    throw new ApiError(response.status, message, body);
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
