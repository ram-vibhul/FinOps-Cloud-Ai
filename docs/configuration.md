# Configuration reference

Generated from `backend/src/costlens/config.py` by `tools/config_reference.py`; do not edit by hand.

Every setting is an environment variable with the `COSTLENS_` prefix (also read from a `.env` file). Secrets are never logged. Outside local mode `COSTLENS_SECRET_KEY` and `COSTLENS_ENCRYPTION_KEYS` are required.

| Variable | Default | What it does |
|---|---|---|
| `COSTLENS_DEPLOYMENT_MODE` | `local` | How this instance runs: local (laptop, trial), saas (hosted by Yugiverse, billing), self_hosted (licence key). |
| `COSTLENS_ENVIRONMENT_NAME` | `development` | A label for logs and error reports, e.g. production or staging. |
| `COSTLENS_TRIAL_DAYS` | `14` | Free trial for new SaaS organisations, in days (0 = none). The length is a placeholder until plans and prices are decided (tracker Q2). |
| `COSTLENS_PLANS_FILE` | *(unset)* | Plan catalogue (B.1): a JSON file of plans, features and limits. Unset = the packaged placeholder plans (tracker Q2). |
| `COSTLENS_PAYMENT_GRACE_DAYS` | `14` | Failed payments (B.16): days a past-due subscription keeps its plan before it moves to the free plan. The payment provider retries the charge meanwhile. |
| `COSTLENS_ERROR_TRACKING_DSN` | *(secret, unset)* | Error tracking (D.9): a Sentry-protocol DSN, e.g. of a self-hosted GlitchTip. Unset = off. Reports are scrubbed of personal data and secrets. |
| `COSTLENS_HEARTBEAT_URL` | *(unset)* | Called every 5 minutes by the worker (an Uptime Kuma "push" URL), so monitoring notices when background jobs stop. Unset = no heartbeat. |
| `COSTLENS_RAZORPAY_WEBHOOK_SECRET` | *(secret, unset)* | Payment webhooks (B.5): the signing secrets from the Razorpay and Stripe dashboards. Unset = that provider's webhook endpoint answers 404. |
| `COSTLENS_STRIPE_WEBHOOK_SECRET` | *(secret, unset)* | Stripe webhook signing secret (whsec_…), see above. |
| `COSTLENS_LICENCE_KEY` | *(secret, unset)* | Self-hosted licence (B.6): the key itself, or a file holding it. It can also be installed with `costlens licence install` or by a platform admin in the UI. |
| `COSTLENS_LICENCE_FILE` | *(unset)* | A file holding the self-hosted licence key, see above. |
| `COSTLENS_BASE_URL` | `http://localhost:8000` | The public address of this instance; used in links, emails and sign-in redirects. |
| `COSTLENS_LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING or ERROR. |
| `COSTLENS_LOG_FORMAT` | `auto` | "json" (one JSON object per line), "text", or "auto" (json unless local mode). |
| `COSTLENS_METRICS_TOKEN` | *(secret, unset)* | Bearer token for /metrics. Without it, /metrics is only served in local mode. |
| `COSTLENS_DATABASE_URL` | `sqlite:///./data/costlens.db` | SQLAlchemy URL: SQLite for local runs, PostgreSQL 17 (postgresql://…) otherwise. |
| `COSTLENS_DATA_DIR` | `./data` | Local files (SQLite database, local-mode encryption key, uploads in local mode). |
| `COSTLENS_MIGRATION_DATABASE_URL` | *(unset)* | PostgreSQL: owner/admin connection used only by `costlens migrate`. The runtime `database_url` should be a separate role without superuser/BYPASSRLS so that row-level security applies; migrate grants it data access. |
| `COSTLENS_ALLOW_PRIVILEGED_DB_ROLE` | `False` | Escape hatch for setups that cannot create a separate role (not recommended). |
| `COSTLENS_DATABASE_ECHO` | `False` | Log every SQL statement (debugging only). |
| `COSTLENS_SECRET_KEY` | *(secret, unset)* | Signs sessions and CSRF tokens. Required outside local mode. |
| `COSTLENS_ENCRYPTION_KEYS` | *(secret, unset)* | Comma-separated Fernet keys (newest first) for secrets stored at rest. |
| `COSTLENS_UI_DIST_DIR` | *(unset)* | Directory holding the built web UI (``frontend/dist``); served at ``/``. |
| `COSTLENS_JOBS_IN_PROCESS` | *(unset)* | Run background jobs inside the web process. Defaults to True on SQLite. |
| `COSTLENS_GOOGLE_CLIENT_ID` | *(unset)* | Sign in with Google (OIDC). Create an OAuth client of type "Web application"; redirect URI: <base_url>/api/v1/auth/callback/google |
| `COSTLENS_GOOGLE_CLIENT_SECRET` | *(secret, unset)* | The Google OAuth client's secret. |
| `COSTLENS_OIDC_PROVIDERS` | *(secret, unset)* | More instance-wide providers, JSON list, e.g. [{"id":"entra","kind":"entra","display_name":"Microsoft","tenant":"organizations", "client_id":"...","client_secret":"..."}, {"id":"okta","kind":"okta","display_name":"Okta","domain":"acme.okta.com", ...}] |
| `COSTLENS_LOCAL_LOGIN` | `False` | Development/trial only: sign in with just an email address, no identity provider. Refused unless deployment mode is local. |
| `COSTLENS_ALLOWED_EMAIL_DOMAINS` | *(empty)* | Only these email domains may sign in (empty = any). Useful for self-hosted. |
| `COSTLENS_SMTP_HOST` | *(unset)* | Outgoing email over SMTP (any provider). Without a host, emails are logged. |
| `COSTLENS_SMTP_PORT` | `587` | SMTP server port. |
| `COSTLENS_SMTP_USERNAME` | *(unset)* | SMTP user name, if the server needs one. |
| `COSTLENS_SMTP_PASSWORD` | *(secret, unset)* | SMTP password. |
| `COSTLENS_SMTP_SECURITY` | `starttls` | starttls, ssl or none. |
| `COSTLENS_EMAIL_FROM` | `CostLens <no-reply@localhost>` | The From address of emails CostLens sends. |
| `COSTLENS_AWS_PRINCIPAL_ARN` | *(unset)* | AWS: the principal (IAM role/user ARN) customers trust in their read-only role. SaaS: Yugiverse's collector role. Self-hosted: the role your CostLens runs as. |
| `COSTLENS_AWS_REGION` | `us-east-1` | Region for AWS API calls that need one (Cost Explorer, STS). |
| `COSTLENS_AGENT_CHART` | `oci://ghcr.io/costlens/charts/costlens-agent` | Where customers install the Kubernetes agent chart from (shown in install commands). Final registry depends on the GitHub organisation (tracker Q9). |
| `COSTLENS_ANTHROPIC_API_KEY` | *(secret, unset)* | Optional AI summaries. |
| `COSTLENS_AI_MODEL` | `DEFAULT_AI_MODEL` | Claude model used for plain-English explanations and questions. |
| `COSTLENS_CORS_ORIGINS` | *(empty)* | Extra browser origins allowed to call the API (the UI itself needs none). |
| `COSTLENS_FX_PROVIDER` | `ecb` | Exchange rates: "ecb" (free European Central Bank reference rates, fetched daily) or "none" (offline installs; costs then show per currency until rates exist). |
| `COSTLENS_PARTNER_SIGNUP` | `approval` | Partner sign-up (8.13): "approval" (Yugiverse approves each partner), "self_serve" (owners become partners at once) or "off". |
| `COSTLENS_DNS_OVER_HTTPS_URL` | `https://cloudflare-dns.com/dns-query` | Verified email domains (8.2) are checked with DNS-over-HTTPS (JSON API). Point this at your own resolver on private networks. |
| `COSTLENS_DEMO_ENABLED` | `True` | Demo organisation with sample data that anyone signed in can explore read-only. |
| `COSTLENS_RATE_LIMIT_ENABLED` | `True` | Rate limits (requests per minute). Backend: PostgreSQL when used, else memory. |
| `COSTLENS_RATE_LIMIT_AUTH_PER_MINUTE` | `20` | Sign-in attempts per minute per IP address. |
| `COSTLENS_RATE_LIMIT_ANONYMOUS_PER_MINUTE` | `120` | Requests per minute per IP address without a session. |
| `COSTLENS_RATE_LIMIT_USER_PER_MINUTE` | `600` | Requests per minute per signed-in user or API key. |
| `COSTLENS_RATE_LIMIT_WRITE_PER_MINUTE` | `120` | Changes (POST, PUT, PATCH, DELETE) per minute per user or API key. |
