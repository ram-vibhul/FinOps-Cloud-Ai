# CloudCostRadar documentation

| Guide | For |
|---|---|
| [Architecture](architecture.md) | How CloudCostRadar is built: processes, data, tenancy, jobs |
| [Installation](installation.md) | Running CloudCostRadar yourself (Docker Compose) and the hosted (SaaS) setup |
| [Configuration](configuration.md) | Every `CCRADAR_` setting (generated from the code) |
| [Backup and restore](backup.md) | Backups, off-site copies, restoring and the restore drill |
| [Monitoring](monitoring.md) | Error tracking (GlitchTip) and uptime checks (Uptime Kuma) for the hosted service |
| [Security](security.md) | Keys, database roles, cloud access, hardening an installation |
| [Public API](api.md) | API keys, endpoints, errors, rate limits, outgoing webhooks, MCP |
| [Development and releasing](releasing.md) | Local development, tests, migrations, CI and releases |

Product plans and pricing on the website are generated from
`backend/src/ccradar/billing/plans.json`. Legal drafts live in `legal/drafts` and are not
published until reviewed.
