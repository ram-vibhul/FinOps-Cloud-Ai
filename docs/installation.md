# Installation

## Self-hosted with Docker Compose

Needs Docker with Compose and about 2 GB of memory. The stack runs PostgreSQL 17, a
one-shot migration, the web app and the worker.

1. Copy the example settings and fill them in:

   ```bash
   cp .env.example .env
   ```

   - `CCRADAR_BASE_URL`: the address people will use (put a TLS-terminating reverse
     proxy or load balancer in front of port 8000).
   - `CCRADAR_SECRET_KEY` and `CCRADAR_ENCRYPTION_KEYS`: generate them with the commands
     in `.env.example`. **Back up the encryption keys**: without them stored secrets
     (cloud credentials, webhooks) cannot be read.
   - `POSTGRES_PASSWORD` (owner, migrations only) and `CCRADAR_DB_APP_PASSWORD` (runtime
     role, row-level security applies).
   - Sign-in: `CCRADAR_GOOGLE_CLIENT_ID`/`SECRET` or `CCRADAR_OIDC_PROVIDERS`; each
     organisation can also add its own SSO later.
   - Email: the `CCRADAR_SMTP_*` settings and `CCRADAR_EMAIL_FROM`.

2. Start it:

   ```bash
   docker compose up -d
   ```

3. Sign in once, then make yourself an administrator of the installation:

   ```bash
   docker compose exec web ccradar platform-admin add you@example.com
   ```

4. Install a licence key if you have one (otherwise the free tier applies):

   ```bash
   docker compose exec web ccradar licence install /path/in/container/licence.txt
   ```

   or set `CCRADAR_LICENCE_KEY`, or paste it in the app.

### Updating

Pull the new image tag and run `docker compose up -d`; the `migrate` service applies
database migrations before web and worker start. Read the release notes first.

### Kubernetes clusters

In the app, Settings → Kubernetes adds a cluster and shows the Helm command for the
read-only agent. The agent only needs outbound HTTPS to `CCRADAR_BASE_URL`.

### AWS access from a self-hosted installation

Customers' read-only roles trust the IAM principal set in `CCRADAR_AWS_PRINCIPAL_ARN`;
the installation uses the normal AWS credential chain (instance role, environment).

## Self-hosted on Kubernetes

`deploy/kubernetes` holds plain manifests applied with kustomize: a namespace with the
restricted pod-security level, a migration Job, the web Deployment (2 replicas, probes,
disruption budget, Service, example Ingress), the worker Deployment, network policies
and a shared data volume. Every pod runs as a non-root user with a read-only root
filesystem and no capabilities.

1. Provide PostgreSQL 17 with an owner role and a runtime role (as in Compose).
2. `cp deploy/kubernetes/ccradar.env.example deploy/kubernetes/ccradar.env`, fill it
   in (it becomes a Secret; never commit it).
3. Set the image tag in `kustomization.yaml` and the host, ingress class and TLS secret
   in `ingress.yaml`. The data volume needs a `ReadWriteMany` storage class (EFS, Azure
   Files, Filestore, NFS) because web and worker pods share uploaded files.
4. `kubectl apply -k deploy/kubernetes`
5. Upgrades: change the tag, delete the finished `ccradar-migrate` Job, apply again.

## Hosted (SaaS) setup

The hosted service uses the same image with `CCRADAR_DEPLOYMENT_MODE=saas`. Hosting
design (regions, ECS or EKS, database) is still open (tracker Q7). The checklist:

- PostgreSQL 17 with separate owner and runtime roles, as in Compose.
- `CCRADAR_SECRET_KEY`, `CCRADAR_ENCRYPTION_KEYS` and payment webhook secrets from a
  secrets manager; never in images or the repository.
- One or more web replicas behind TLS, and at least one worker.
- `CCRADAR_AWS_PRINCIPAL_ARN` for the collector role (Q11), the Google OAuth client (Q10).
- Trial length (`CCRADAR_TRIAL_DAYS`) and the plan catalogue (`CCRADAR_PLANS_FILE`).
- `CCRADAR_METRICS_TOKEN` for Prometheus scraping.

## Local development

See [Development and releasing](releasing.md).
