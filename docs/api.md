# Public API

The API behind the CloudCostRadar app is public: everything the UI does, you can script.
Interactive documentation (OpenAPI) is at `<base url>/docs`; the schema at
`/openapi.json`.

## Authentication

Create an API key in Settings → API keys (admins; plans with the API keys feature). A key
looks like `clk_…`, is shown once, and has a scope:

| Scope | Can |
|---|---|
| `read` | Read costs, savings, budgets, Kubernetes data |
| `write` | Also change budgets, savings status, views and similar |
| `admin` | Also organisation settings |

Send it as a bearer token:

```bash
curl -H "Authorization: Bearer clk_..." https://ccradar.example.com/api/v1/costs/summary
```

Keys belong to one organisation, can expire, can be revoked, and record when they were
last used. Requests with a key need no CSRF token.

## Conventions

- JSON in and out; dates `YYYY-MM-DD`, times ISO 8601 UTC; money as numbers with a
  separate currency code.
- Errors are RFC 9457 problem details with a stable `code`:

  ```json
  {"type": "about:blank", "title": "Payment Required", "status": 402,
   "code": "plan_limit", "detail": "The Free plan allows 1 cloud and SaaS connections; ..."}
  ```

  Common codes: `not_found`, `forbidden`, `demo_read_only`, `plan_feature`, `plan_limit`,
  `rate_limited`, `validation_error`.
- Long lists such as the audit log and notifications are paged (`next_cursor`, `next_before`).

## Rate limits

Per key: `CCRADAR_RATE_LIMIT_USER_PER_MINUTE` requests and
`CCRADAR_RATE_LIMIT_WRITE_PER_MINUTE` changes a minute. Every answer carries
`RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset`; a `429` adds
`Retry-After`.

## Useful endpoints

| Endpoint | What |
|---|---|
| `GET /api/v1/costs/summary` | This month, forecast and trend |
| `POST /api/v1/costs/explore` | Group and filter costs (dimensions, granularity, period) |
| `GET /api/v1/findings` | Savings, ranked; filter by category, status, owner |
| `GET /api/v1/budgets` | Budgets and their status |
| `GET /api/v1/costs/anomalies` | Unusual increases |
| `GET /api/v1/statements/{month}/focus.csv` | A month of costs in FOCUS format |
| `POST /api/v1/estimates/terraform` | Monthly cost change of a Terraform plan (CI) |
| `GET /api/v1/org/plan` | Plan, features and usage against limits |

## Outgoing webhooks

Settings → Notifications → Outgoing webhooks sends events (anomalies, budget alerts,
approvals waiting, imports, …) to your HTTPS endpoint:

- Body: `{"id", "event", "created_at", "org_id", "data": {"title", "text", "link", "severity"}}`.
- Headers: `X-CloudCostRadar-Event`, `X-CloudCostRadar-Delivery` (de-duplicate on it) and
  `X-CloudCostRadar-Signature: t=<unix seconds>,v1=<hex>`, where the hex is HMAC-SHA256 with the
  webhook's secret over `"<t>.<raw body>"`. Reject timestamps older than 5 minutes.
- A 2xx answer means delivered; otherwise retried after 1 min, 5 min, 30 min, 2 h and 6 h.

## AI assistants (MCP)

`POST /mcp` speaks the Model Context Protocol (JSON-RPC) with an API key, so assistants
can answer questions about your costs. Tools: `cost_summary`, `explore_costs`,
`top_cost_changes`, `list_savings`, `list_anomalies`, `budgets`; keys allowed to start
investigations also get `create_investigation`. Everything else is read-only.
