# Configuration reference

Generated from `backend/src/ccradar/config.py` by `tools/config_reference.py`; do not edit by hand.

Every setting is an environment variable with the `CCRADAR_` prefix (also read from a `.env` file). Secrets are never logged. Outside local mode `CCRADAR_SECRET_KEY` and `CCRADAR_ENCRYPTION_KEYS` are required.

| Variable | Default | What it does |
|---|---|---|
| `CCRADAR_DEPLOYMENT_MODE` | `local` | How this instance runs: local (laptop, trial), saas (hosted by Yugiverse, billing), self_hosted (licence key). |
| `CCRADAR_ENVIRONMENT_NAME` | `development` | A label for logs and error reports, e.g. production or staging. |
| `CCRADAR_TRIAL_DAYS` | `14` | Free trial for new SaaS organisations, in days (0 = none). The length is a placeholder until plans and prices are decided (tracker Q2). |
| `CCRADAR_PLANS_FILE` | *(unset)* | Plan catalogue (B.1): a JSON file of plans, features and limits. Unset = the packaged placeholder plans (tracker Q2). |
| `CCRADAR_PAYMENT_GRACE_DAYS` | `14` | Failed payments (B.16): days a past-due subscription keeps its plan before it moves to the free plan. The payment provider retries the charge meanwhile. |
| `CCRADAR_ERROR_TRACKING_DSN` | *(secret, unset)* | Error tracking (D.9): a Sentry-protocol DSN, e.g. of a self-hosted GlitchTip. Unset = off. Reports are scrubbed of personal data and secrets. |
| `CCRADAR_HEARTBEAT_URL` | *(unset)* | Called every 5 minutes by the worker (an Uptime Kuma "push" URL), so monitoring notices when background jobs stop. Unset = no heartbeat. |
| `CCRADAR_RAZORPAY_WEBHOOK_SECRET` | *(secret, unset)* | Payment webhooks (B.5): the signing secrets from the Razorpay and Stripe dashboards. Unset = that provider's webhook endpoint answers 404. |
| `CCRADAR_STRIPE_WEBHOOK_SECRET` | *(secret, unset)* | Stripe webhook signing secret (whsec_…), see above. |
| `CCRADAR_LICENCE_KEY` | *(secret, unset)* | Self-hosted licence (B.6): the key itself, or a file holding it. It can also be installed with `ccradar licence install` or by a platform admin in the UI. |
| `CCRADAR_LICENCE_FILE` | *(unset)* | A file holding the self-hosted licence key, see above. |
| `CCRADAR_BASE_URL` | `http://localhost:8000` | The public address of this instance; used in links, emails and sign-in redirects. |
| `CCRADAR_LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING or ERROR. |
| `CCRADAR_LOG_FORMAT` | `auto` | "json" (one JSON object per line), "text", or "auto" (json unless local mode). |
| `CCRADAR_METRICS_TOKEN` | *(secret, unset)* | Bearer token for /metrics. Without it, /metrics is only served in local mode. |
| `CCRADAR_DATABASE_URL` | `sqlite:///./data/ccradar.db` | SQLAlchemy URL: SQLite for local runs, PostgreSQL 17 (postgresql://…) otherwise. |
| `CCRADAR_DATA_DIR` | `./data` | Local files (SQLite database, local-mode encryption key, uploads in local mode). |
| `CCRADAR_MIGRATION_DATABASE_URL` | *(unset)* | PostgreSQL: owner/admin connection used only by `ccradar migrate`. The runtime `database_url` should be a separate role without superuser/BYPASSRLS so that row-level security applies; migrate grants it data access. |
| `CCRADAR_ALLOW_PRIVILEGED_DB_ROLE` | `False` | Escape hatch for setups that cannot create a separate role (not recommended). |
| `CCRADAR_DATABASE_ECHO` | `False` | Log every SQL statement (debugging only). |
| `CCRADAR_SECRET_KEY` | *(secret, unset)* | Signs sessions and CSRF tokens. Required outside local mode. |
| `CCRADAR_ENCRYPTION_KEYS` | *(secret, unset)* | Comma-separated Fernet keys (newest first) for secrets stored at rest. |
| `CCRADAR_UI_DIST_DIR` | *(unset)* | Directory holding the built web UI (``frontend/dist``); served at ``/``. |
| `CCRADAR_JOBS_IN_PROCESS` | *(unset)* | Run background jobs inside the web process. Defaults to True on SQLite. |
| `CCRADAR_GOOGLE_CLIENT_ID` | *(unset)* | Sign in with Google (OIDC). Create an OAuth client of type "Web application"; redirect URI: <base_url>/api/v1/auth/callback/google |
| `CCRADAR_GOOGLE_CLIENT_SECRET` | *(secret, unset)* | The Google OAuth client's secret. |
| `CCRADAR_OIDC_PROVIDERS` | *(secret, unset)* | More instance-wide providers, JSON list, e.g. [{"id":"entra","kind":"entra","display_name":"Microsoft","tenant":"organizations", "client_id":"...","client_secret":"..."}, {"id":"okta","kind":"okta","display_name":"Okta","domain":"acme.okta.com", ...}] |
| `CCRADAR_LOCAL_LOGIN` | `False` | Development/trial only: sign in with just an email address, no identity provider. Refused unless deployment mode is local. |
| `CCRADAR_ALLOWED_EMAIL_DOMAINS` | *(empty)* | Only these email domains may sign in (empty = any). Useful for self-hosted. |
| `CCRADAR_SMTP_HOST` | *(unset)* | Outgoing email over SMTP (any provider). Without a host, emails are logged. |
| `CCRADAR_SMTP_PORT` | `587` | SMTP server port. |
| `CCRADAR_SMTP_USERNAME` | *(unset)* | SMTP user name, if the server needs one. |
| `CCRADAR_SMTP_PASSWORD` | *(secret, unset)* | SMTP password. |
| `CCRADAR_SMTP_SECURITY` | `starttls` | starttls, ssl or none. |
| `CCRADAR_EMAIL_FROM` | `CloudCostRadar <no-reply@localhost>` | The From address of emails CloudCostRadar sends. |
| `CCRADAR_AWS_PRINCIPAL_ARN` | *(unset)* | AWS: the principal (IAM role/user ARN) customers trust in their read-only role. SaaS: Yugiverse's collector role. Self-hosted: the role your CloudCostRadar runs as. |
| `CCRADAR_AWS_REGION` | `us-east-1` | Region for AWS API calls that need one (Cost Explorer, STS). |
| `CCRADAR_AGENT_CHART` | `oci://ghcr.io/yugiverse/charts/cloudcostradar-agent` | Where customers install the Kubernetes agent chart from (shown in install commands). Final registry depends on the GitHub organisation (tracker Q9). |
| `CCRADAR_ANTHROPIC_API_KEY` | *(secret, unset)* | Optional AI summaries. |
| `CCRADAR_AI_MODEL` | `DEFAULT_AI_MODEL` | Claude model used for plain-English explanations and questions. |
| `CCRADAR_CORS_ORIGINS` | *(empty)* | Extra browser origins allowed to call the API (the UI itself needs none). |
| `CCRADAR_FX_PROVIDER` | `ecb` | Exchange rates: "ecb" (free European Central Bank reference rates, fetched daily) or "none" (offline installs; costs then show per currency until rates exist). |
| `CCRADAR_PARTNER_SIGNUP` | `approval` | Partner sign-up (8.13): "approval" (Yugiverse approves each partner), "self_serve" (owners become partners at once) or "off". |
| `CCRADAR_DNS_OVER_HTTPS_URL` | `https://cloudflare-dns.com/dns-query` | Verified email domains (8.2) are checked with DNS-over-HTTPS (JSON API). Point this at your own resolver on private networks. |
| `CCRADAR_DEMO_ENABLED` | `True` | Demo organisation with sample data that anyone signed in can explore read-only. |
| `CCRADAR_RATE_LIMIT_ENABLED` | `True` | Rate limits (requests per minute). Backend: PostgreSQL when used, else memory. |
| `CCRADAR_RATE_LIMIT_AUTH_PER_MINUTE` | `20` | Sign-in attempts per minute per IP address. |
| `CCRADAR_RATE_LIMIT_ANONYMOUS_PER_MINUTE` | `120` | Requests per minute per IP address without a session. |
| `CCRADAR_RATE_LIMIT_USER_PER_MINUTE` | `600` | Requests per minute per signed-in user or API key. |
| `CCRADAR_RATE_LIMIT_WRITE_PER_MINUTE` | `120` | Changes (POST, PUT, PATCH, DELETE) per minute per user or API key. |
