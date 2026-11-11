# Backup and restore (self-hosted)

CloudCostRadar keeps everything in two places: the database and the data folder
(`CCRADAR_DATA_DIR`, `/data` in containers) with uploaded cost files and generated
reports. `ccradar backup` puts both in one `.tar.gz` file.

## What is not in a backup

**The encryption keys** (`CCRADAR_ENCRYPTION_KEYS`) and the signing key
(`CCRADAR_SECRET_KEY`). Keep them separately, for example in a password manager.
A restored database without its encryption keys still works, but stored secrets
(cloud credentials, webhook addresses, SSO client secrets) cannot be read and must be
entered again.

## Making a backup

```bash
docker compose run --rm migrate ccradar backup create /data/backups/ccradar-$(date +%F).tar.gz
```

Backups run through the `migrate` service because it holds the database owner connection
(`CCRADAR_MIGRATION_DATABASE_URL`); the web and worker processes deliberately use a
runtime role that cannot read everything.

- SQLite: an online copy, safe while CloudCostRadar runs.
- PostgreSQL: `pg_dump` in custom format through the owner connection
  (`CCRADAR_MIGRATION_DATABASE_URL`). The image includes the PostgreSQL 17 client.
- The file is created readable by its owner only: it contains your cost data and
  encrypted secrets.

Check a backup any time:

```bash
ccradar backup verify FILE
```

## Schedule and off-site copies

- Run a backup **every day** and keep at least 14 daily and 6 monthly copies.
- Copy each backup **off the server** (another region, another provider or offline
  storage), encrypted, for example with `age` or `gpg` before upload.
- Compose: a host cron job running the `backup create` command above, followed by your
  copy command. Kubernetes: a CronJob using the CloudCostRadar image with `args: ["ccradar",
  "backup", "create", "/backups/…"]`, the same Secret (it holds
  `CCRADAR_MIGRATION_DATABASE_URL`), and a volume for the backups.
- Your database provider's own snapshots (e.g. managed PostgreSQL) are a good second
  layer, but keep these files too: they also hold the uploaded files and restore into any
  PostgreSQL.

## Restoring

1. Stop the web and worker processes (`docker compose stop web worker`).
2. Make sure the installation has the **same encryption keys** as when the backup was made.
3. Restore:

   ```bash
   docker compose run --rm migrate ccradar restore /data/backups/FILE.tar.gz --yes
   ```

   - CloudCostRadar first writes a safety backup of the current data to
     `<data dir>/pre-restore-<time>.tar.gz`.
   - The database is replaced (PostgreSQL: the `public` schema is recreated, so the owner
     role must own the database), the current files folder is kept as
     `files.before-restore-<time>`, and migrations run, which also upgrades an older
     backup to this version. A backup from a newer CloudCostRadar is refused.

4. Start CloudCostRadar again (`docker compose up -d`) and sign in to check.

## Restore drill

Test a restore **every three months** on a separate machine or namespace:

1. Install the same CloudCostRadar version with the production encryption keys.
2. Restore last night's backup.
3. Check: you can sign in, the organisations and the latest costs are there, a cloud
   connection's "Check permissions" works (proves the keys match), and an uploaded file
   opens.
4. Write down how long it took; that is your recovery time.
