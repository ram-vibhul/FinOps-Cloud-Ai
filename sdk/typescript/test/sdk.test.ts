import assert from "node:assert/strict";
import { test } from "node:test";
import { clean, fingerprint, Usage } from "../src/index.ts";

const PROMPT = "Summarise this contract for ACME Corp: the secret price is 12345 rupees.";
const SALT = new TextEncoder().encode("customer-side-salt");

function fake(answers: (number | Error)[] = []) {
  const calls: { url: string; body: { events: Record<string, unknown>[] }; headers: Record<string, string> }[] = [];
  const transport = async (url: string, body: string, headers: Record<string, string>) => {
    calls.push({ url, body: JSON.parse(body), headers });
    const a = answers.shift() ?? 202;
    if (a instanceof Error) throw a;
    return a;
  };
  return { calls, transport };
}

test("prompt text is never sent, only a salted fingerprint", async () => {
  const f = fake();
  const u = new Usage({ apiKey: "clai_test", url: "https://costlens.example/", salt: SALT, transport: f.transport, team: "payments team" });
  const e = u.record("anthropic", "claude-sonnet-5-5", { inputTokens: 1200, outputTokens: 300, prompt: PROMPT });
  await u.flush();
  const raw = JSON.stringify(f.calls[0].body);
  for (const secret of ["contract", "ACME", "12345", "rupees", "Summarise"]) assert.ok(!raw.includes(secret), secret);
  assert.equal(f.calls[0].url, "https://costlens.example/api/v1/ingest/ai-usage");
  assert.equal(f.calls[0].headers.Authorization, "Bearer clai_test");
  assert.equal(e.fingerprint, fingerprint(PROMPT, SALT));
  assert.equal((e.fingerprint as string).length, 32);
  assert.equal(f.calls[0].body.events[0].team, "payments-team");
  assert.equal(fingerprint(`  ${PROMPT.replaceAll(" ", "\n  ")}`, SALT), e.fingerprint);
});

test("fingerprints match the Python SDK", () => {
  // Same algorithm in both SDKs: HMAC-SHA256(salt, whitespace-normalised prompt)[:32].
  assert.equal(fingerprint("hello   world", SALT), fingerprint("hello world", SALT));
  assert.equal(fingerprint("hello world", SALT), "3fba58ec5da16dca8541127849e4f65e"); // value from the Python SDK test
  assert.match(fingerprint("x", SALT), /^[0-9a-f]{32}$/);
});

test("names cannot carry sentences", () => {
  const u = new Usage({ apiKey: "clai_test", url: "https://x", transport: fake().transport });
  const e = u.record("openai", "gpt-4o", { feature: `Please summarise: ${PROMPT}` });
  assert.ok(!(e.feature as string).includes(" "));
  assert.equal(clean("   "), undefined);
});

test("response helpers", () => {
  const u = new Usage({ apiKey: "clai_test", url: "https://x", transport: fake().transport });
  const a = u.fromAnthropic(
    { model: "claude-opus-5-5", usage: { input_tokens: 100, output_tokens: 50, cache_read_input_tokens: 900, cache_creation_input_tokens: 10 } },
    { feature: "chat", latencyMs: 812 },
  );
  assert.deepEqual([a.input_tokens, a.cache_read_tokens, a.cache_write_tokens, a.latency_ms], [100, 900, 10, 812]);
  const o = u.fromOpenAI({ model: "gpt-4o-mini", usage: { prompt_tokens: 1000, completion_tokens: 20, prompt_tokens_details: { cached_tokens: 600 } } }, { retry: true });
  assert.deepEqual([o.input_tokens, o.cache_read_tokens, o.output_tokens, o.retry], [400, 600, 20, true]);
});

test("batching, retries and never throwing", async () => {
  const f = fake([new Error("down"), 503, 202]);
  const u = new Usage({ apiKey: "clai_test", url: "https://x", transport: f.transport, batchSize: 50 });
  u.record("anthropic", "m");
  u.record("anthropic", "m");
  assert.equal(await u.flush(), false); // network error, kept
  assert.equal(await u.flush(), false); // 503, kept
  assert.equal(await u.flush(), true); // 202
  assert.deepEqual(f.calls.map((c) => c.body.events.length), [2, 2, 2]);
  const r = fake([401]);
  const u2 = new Usage({ apiKey: "clai_test", url: "https://x", transport: r.transport });
  u2.record("anthropic", "m");
  assert.equal(await u2.flush(), true); // rejected batch dropped
  assert.throws(() => new Usage({ apiKey: "sk-1", url: "https://x" }), /clai_/);
});
