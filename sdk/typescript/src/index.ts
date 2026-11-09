/**
 * Report AI/LLM usage to CostLens.
 *
 *   import { Usage } from "@costlens/ai";
 *   const usage = new Usage({ apiKey: process.env.COSTLENS_AI_KEY!, url: "https://costlens.example.com", app: "checkout" });
 *   const res = await anthropic.messages.create({ ... });
 *   usage.fromAnthropic(res, { feature: "summaries", prompt });
 *   await usage.close();
 *
 * Sent: provider, model, token counts, optional cost, app/team/feature/environment names,
 * retry/error flags, latency, and a salted HMAC-SHA256 fingerprint of the prompt. The salt
 * stays in your process. Prompt and completion text are never sent. Reporting never throws
 * into your code.
 */
import { createHmac, randomBytes } from "node:crypto";

export const VERSION = "0.1.0";
const IDENT = /^[\w.:/@+-]{1,100}$/;
const MAX_BUFFER = 10_000;
const MAX_ATTEMPTS = 3;

export type Transport = (url: string, body: string, headers: Record<string, string>) => Promise<number>;

export interface UsageOptions {
  apiKey: string;
  url: string;
  app?: string;
  team?: string;
  environment?: string;
  /** Keep the same salt across processes to match repeated prompts across them. Never sent. */
  salt?: Uint8Array;
  batchSize?: number;
  transport?: Transport;
}

export interface RecordInput {
  inputTokens?: number;
  outputTokens?: number;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
  costUsd?: number;
  feature?: string;
  team?: string;
  app?: string;
  retry?: boolean;
  error?: boolean;
  latencyMs?: number;
  /** Only used to make the fingerprint; never sent. */
  prompt?: string;
  at?: Date;
}

export type UsageEvent = Record<string, string | number | boolean>;

export function clean(value: string | undefined, limit = 100): string | undefined {
  if (value === undefined || value === null) return undefined;
  const s = String(value).trim().replace(/[^\w.:/@+-]+/g, "-").slice(0, limit).replace(/^-+|-+$/g, "");
  return s && IDENT.test(s) ? s : undefined;
}

export function fingerprint(prompt: string, salt: Uint8Array): string {
  const normal = prompt.split(/\s+/).filter(Boolean).join(" ");
  return createHmac("sha256", salt).update(normal).digest("hex").slice(0, 32);
}

const fetchTransport: Transport = async (url, body, headers) => {
  if (!url.startsWith("https://") && !url.startsWith("http://localhost")) throw new Error("CostLens URL must use https");
  const res = await fetch(url, { method: "POST", body, headers, signal: AbortSignal.timeout(10_000) });
  return res.status;
};

const n = (v: unknown): number => Math.max(0, Math.trunc(Number(v) || 0));

export class Usage {
  readonly #key: string;
  readonly #endpoint: string;
  readonly #defaults: { app?: string; team?: string; environment?: string };
  readonly #salt: Uint8Array;
  readonly #batch: number;
  readonly #send: Transport;
  #buffer: UsageEvent[] = [];
  #failures = 0;

  constructor(opts: UsageOptions) {
    if (!opts.apiKey?.startsWith("clai_")) throw new Error("use a CostLens AI usage key (starts with clai_)");
    this.#key = opts.apiKey;
    this.#endpoint = `${opts.url.replace(/\/+$/, "")}/api/v1/ingest/ai-usage`;
    this.#defaults = { app: clean(opts.app), team: clean(opts.team), environment: clean(opts.environment, 40) };
    this.#salt = opts.salt ?? randomBytes(32);
    this.#batch = Math.max(1, Math.min(opts.batchSize ?? 100, 1000));
    this.#send = opts.transport ?? fetchTransport;
  }

  /** Queue one event and return exactly what will be sent. */
  record(provider: string, model: string, input: RecordInput = {}): UsageEvent {
    const e: UsageEvent = {
      at: (input.at ?? new Date()).toISOString(),
      provider: clean(provider, 40) ?? "unknown",
      model: clean(model) ?? "unknown",
      input_tokens: n(input.inputTokens),
      output_tokens: n(input.outputTokens),
      cache_read_tokens: n(input.cacheReadTokens),
      cache_write_tokens: n(input.cacheWriteTokens),
      retry: Boolean(input.retry),
      status: input.error ? "error" : "ok",
    };
    const names: [string, string | undefined][] = [
      ["app", clean(input.app) ?? this.#defaults.app],
      ["team", clean(input.team) ?? this.#defaults.team],
      ["feature", clean(input.feature)],
      ["environment", this.#defaults.environment],
    ];
    for (const [k, v] of names) if (v) e[k] = v;
    if (input.costUsd !== undefined) e.cost_usd = Math.round(Math.max(0, input.costUsd) * 1e6) / 1e6;
    if (input.latencyMs !== undefined) e.latency_ms = n(input.latencyMs);
    if (input.prompt) e.fingerprint = fingerprint(input.prompt, this.#salt);
    if (this.#buffer.length >= MAX_BUFFER) this.#buffer.shift();
    this.#buffer.push(e);
    if (this.#buffer.length >= this.#batch) void this.flush();
    return e;
  }

  /** Record an Anthropic Messages API response (reads `response.usage`). */
  fromAnthropic(response: { model?: string; usage?: Record<string, number | null | undefined> }, input: RecordInput = {}): UsageEvent {
    const u = response.usage ?? {};
    return this.record("anthropic", String(response.model ?? "unknown"), {
      ...input,
      inputTokens: n(u.input_tokens),
      outputTokens: n(u.output_tokens),
      cacheReadTokens: n(u.cache_read_input_tokens),
      cacheWriteTokens: n(u.cache_creation_input_tokens),
    });
  }

  /** Record an OpenAI-style chat completion (reads `response.usage`). */
  fromOpenAI(
    response: { model?: string; usage?: { prompt_tokens?: number; completion_tokens?: number; prompt_tokens_details?: { cached_tokens?: number } } },
    input: RecordInput & { provider?: string } = {},
  ): UsageEvent {
    const u = response.usage ?? {};
    const cached = n(u.prompt_tokens_details?.cached_tokens);
    const { provider = "openai", ...rest } = input;
    return this.record(provider, String(response.model ?? "unknown"), {
      ...rest,
      inputTokens: Math.max(0, n(u.prompt_tokens) - cached),
      outputTokens: n(u.completion_tokens),
      cacheReadTokens: cached,
    });
  }

  /** Send what is queued. Resolves true when the buffer is empty afterwards. Never rejects. */
  async flush(): Promise<boolean> {
    const batch = this.#buffer.splice(0, 1000);
    if (batch.length === 0) return true;
    let status = 0;
    try {
      status = await this.#send(this.#endpoint, JSON.stringify({ events: batch }), {
        Authorization: `Bearer ${this.#key}`,
        "Content-Type": "application/json",
        "User-Agent": `costlens-ai-js/${VERSION}`,
      });
    } catch {
      status = 0;
    }
    if (status >= 200 && status < 300) {
      this.#failures = 0;
    } else if ([400, 401, 403, 422].includes(status)) {
      // rejected: drop the batch rather than retry forever
    } else if (++this.#failures < MAX_ATTEMPTS) {
      this.#buffer.unshift(...batch);
    } else {
      this.#failures = 0;
    }
    return this.#buffer.length === 0;
  }

  async close(): Promise<void> {
    for (let i = 0; i < MAX_ATTEMPTS; i++) if (await this.flush()) return;
  }
}
