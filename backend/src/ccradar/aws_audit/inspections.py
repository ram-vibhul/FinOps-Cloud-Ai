"""Audit inspections: specific things for a person to review where no saving can be
estimated reliably (e.g. a group that never scales). Each returns short notes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ccradar.findings.aws_db import _non_prod
from ccradar.models import CloudResource

Objects = dict[str, list[CloudResource]]
MAX_NAMES = 5
GLACIER_OVERHEAD = 0.25  # retrieval + early deletion above this share of archive storage
BACKUP_LONG_DAYS = 365


class FactsLike(Protocol):
    objects: dict[str, list[CloudResource]]
    currency: str

    def bucket_costs(self) -> dict[str, dict[str, float]]: ...

    def usage_costs(
        self, ids: list[str], classify: Callable[[str | None], str]
    ) -> dict[str, dict[str, float]]: ...

    def pattern_costs(self, patterns: dict[str, str]) -> dict[str, float]: ...


def _names(rows: list[CloudResource]) -> str:
    names = [r.name or r.resource_id for r in rows[:MAX_NAMES]]
    more = len(rows) - len(names)
    return ", ".join(names) + (f" and {more} more" if more > 0 else "")


def static_groups(facts: FactsLike) -> list[str]:
    objects = facts.objects
    rows = [
        g
        for g in objects.get("autoscaling_group", [])
        if not (g.attributes or {}).get("eks_nodegroup")
        and (a := g.attributes or {}).get("min") == a.get("max")
        and (a.get("desired") or 0) >= 2
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} Auto Scaling group(s) never scale (minimum = maximum): {_names(rows)}. "
        "Add a scaling policy or a night/weekend schedule."
    ]


def fargate_spot(facts: FactsLike) -> list[str]:
    objects = facts.objects
    rows = [
        s
        for s in objects.get("ecs_service", [])
        if (a := s.attributes or {}).get("launch_type") == "FARGATE"
        and (a.get("desired") or 0) > 0
        and "FARGATE_SPOT" not in (a.get("capacity_providers") or [])
        and (
            _non_prod(s)
            or any(w in str(a.get("cluster", "")).lower() for w in ("dev", "test", "staging"))
        )
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} non-production Fargate service(s) without Fargate Spot (about 70% "
        f"cheaper): {_names(rows)}."
    ]


def batch_environments(facts: FactsLike) -> list[str]:
    objects = facts.objects
    envs = [
        e
        for e in objects.get("batch_compute_environment", [])
        if (e.attributes or {}).get("managed")
    ]
    warm = [e for e in envs if ((e.attributes or {}).get("min_vcpus") or 0) > 0]
    on_demand = [e for e in envs if (e.attributes or {}).get("compute_type") in ("EC2", "FARGATE")]
    notes = []
    if warm:
        notes.append(
            f"{len(warm)} Batch environment(s) keep instances running between jobs "
            f"(minimum vCPUs above 0): {_names(warm)}."
        )
    if on_demand:
        notes.append(
            f"{len(on_demand)} Batch environment(s) use On-Demand capacity; most batch jobs "
            f"can run on Spot: {_names(on_demand)}."
        )
    return notes


def ecr_policies(facts: FactsLike) -> list[str]:
    objects = facts.objects
    rows = [
        r
        for r in objects.get("ecr_repository", [])
        if (r.attributes or {}).get("lifecycle_policy") is False
    ]
    if not rows:
        return []
    return [f"{len(rows)} repositories have no lifecycle policy: {_names(rows)}."]


def s3_versions(facts: FactsLike) -> list[str]:
    rows = [
        b
        for b in facts.objects.get("s3_bucket", [])
        if (a := b.attributes or {}).get("versioning") == "Enabled"
        and a.get("lifecycle_rules") is not None
        and not a.get("expires_noncurrent")
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} versioned bucket(s) keep old object versions forever (no "
        f"noncurrent-version expiry): {_names(rows)}."
    ]


def s3_multipart(facts: FactsLike) -> list[str]:
    rows = [
        b
        for b in facts.objects.get("s3_bucket", [])
        if (a := b.attributes or {}).get("lifecycle_rules") is not None
        and not a.get("aborts_multipart")
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} bucket(s) never clean up incomplete multipart uploads (billed as "
        f"storage but invisible in listings): {_names(rows)}. Add a rule that aborts them "
        "after 7 days."
    ]


def glacier(facts: FactsLike) -> list[str]:
    notes = []
    for bucket, c in sorted(facts.bucket_costs().items()):
        archive = c.get("archive", 0.0)
        overhead = c.get("retrieval", 0.0) + c.get("early_delete", 0.0)
        if archive > 0 and overhead > archive * GLACIER_OVERHEAD and overhead >= 1:
            notes.append(
                f"{bucket}: retrievals and early deletions cost {overhead:,.2f} "
                f"{facts.currency} a month against {archive:,.2f} of archive storage; this "
                "data may be read too often for Glacier."
            )
    return notes[:MAX_NAMES]


def ebs_iops(facts: FactsLike) -> list[str]:
    rows = [
        v
        for v in facts.objects.get("ebs_volume", [])
        if (v.attributes or {}).get("volume_type") in ("io1", "io2")
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} Provisioned IOPS volume(s) (io1/io2) pay for IOPS whether used or not: "
        f"{_names(rows)}. Compare with CloudWatch VolumeReadOps/VolumeWriteOps; gp3 gives "
        "up to 16,000 IOPS for less."
    ]


def efs_throughput(facts: FactsLike) -> list[str]:
    rows = [
        f
        for f in facts.objects.get("efs_file_system", [])
        if (f.attributes or {}).get("throughput_mode") == "provisioned"
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} EFS file system(s) pay for provisioned throughput: {_names(rows)}. "
        "Elastic throughput often costs less for spiky use."
    ]


def fsx(facts: FactsLike) -> list[str]:
    systems = facts.objects.get("fsx_file_system", [])
    multi = [
        f
        for f in systems
        if "MULTI_AZ" in str((f.attributes or {}).get("deployment") or "") and _non_prod(f)
    ]
    lustre = [
        f
        for f in systems
        if (a := f.attributes or {}).get("type") == "LUSTRE"
        and a.get("compression") in (None, "NONE")
    ]
    notes = []
    if multi:
        notes.append(
            f"{len(multi)} non-production FSx file system(s) are Multi-AZ: {_names(multi)}."
        )
    if lustre:
        notes.append(
            f"{len(lustre)} FSx for Lustre file system(s) without data compression (LZ4 "
            f"usually halves the storage needed): {_names(lustre)}."
        )
    return notes


def backup_retention(facts: FactsLike) -> list[str]:
    forever, warm = [], []
    for p in facts.objects.get("backup_plan", []):
        for r in (p.attributes or {}).get("rules") or []:
            label = f"{p.name}/{r.get('name')}"
            if not r.get("delete_after_days"):
                forever.append(label)
            elif r["delete_after_days"] > BACKUP_LONG_DAYS and not r.get("cold_after_days"):
                warm.append(label)
    notes = []
    if forever:
        notes.append(
            f"{len(forever)} backup rule(s) never delete recovery points: "
            f"{', '.join(forever[:MAX_NAMES])}."
        )
    if warm:
        notes.append(
            f"{len(warm)} rule(s) keep backups over a year without moving them to cold "
            f"storage: {', '.join(warm[:MAX_NAMES])}."
        )
    return notes


NON_PROD_WORDS = ("dev", "test", "staging", "sandbox", "qa")
PREVIOUS_SEARCH = ("m3.", "m4.", "r3.", "r4.", "t2.", "c4.", "i2.")
GRAVITON_LATER = ("cache.m5.", "cache.r5.", "cache.m6i.", "cache.r6i.", "cache.t3.")


def _nonprod(r: CloudResource) -> bool:
    return bool(_non_prod(r)) or any(w in (r.name or "").lower() for w in NON_PROD_WORDS)


def rds_licensing(facts: FactsLike) -> list[str]:
    rows = [
        d
        for d in facts.objects.get("rds_instance", [])
        if str((a := d.attributes or {}).get("engine", "")).endswith("-ee")
        and a.get("license_model") in (None, "license-included")
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} database(s) run an Enterprise edition with the licence included: "
        f"{_names(rows)}. Standard edition costs far less if its features are enough."
    ]


def aurora_serverless(facts: FactsLike) -> list[str]:
    rows = [
        c
        for c in facts.objects.get("rds_cluster", [])
        if ((c.attributes or {}).get("serverless_min_acu") or 0) >= 2 and _nonprod(c)
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} non-production Aurora Serverless cluster(s) never scale below 2 ACU: "
        f"{_names(rows)}. A minimum of 0.5 (or 0 with auto-pause) costs less when idle."
    ]


def dynamodb_capacity(facts: FactsLike) -> list[str]:
    from ccradar.findings.aws_databases import dynamodb_class

    tables = facts.objects.get("dynamodb_table", [])
    on_demand = [t for t in tables if (t.attributes or {}).get("billing_mode") == "PAY_PER_REQUEST"]
    costs = facts.usage_costs([t.resource_id for t in on_demand], dynamodb_class)
    steady = [t for t in on_demand if costs.get(t.resource_id, {}).get("throughput", 0) >= 100]
    if not steady:
        return []
    return [
        f"{len(steady)} on-demand table(s) spend 100+ {facts.currency} a month on reads and "
        f"writes: {_names(steady)}. Steady traffic is usually much cheaper provisioned with "
        "auto scaling; check the traffic pattern first."
    ]


def elasticache_graviton(facts: FactsLike) -> list[str]:
    rows = [
        c
        for c in facts.objects.get("elasticache_cluster", [])
        if str((c.attributes or {}).get("node_type") or "").startswith(GRAVITON_LATER)
    ]
    if not rows:
        return []
    return [f"{len(rows)} cache cluster(s) could move to Graviton nodes (m7g/r7g): {_names(rows)}."]


def opensearch(facts: FactsLike) -> list[str]:
    domains = facts.objects.get("opensearch_domain", [])
    gp2 = [d for d in domains if (d.attributes or {}).get("volume_type") == "gp2"]
    old = [
        d
        for d in domains
        if any(p in str((d.attributes or {}).get("instance_type") or "") for p in PREVIOUS_SEARCH)
    ]
    masters = [d for d in domains if (d.attributes or {}).get("dedicated_master") and _nonprod(d)]
    notes = []
    if gp2:
        notes.append(f"{len(gp2)} domain(s) on gp2 storage (gp3 is cheaper): {_names(gp2)}.")
    if old:
        notes.append(f"{len(old)} domain(s) on previous-generation instances: {_names(old)}.")
    if masters:
        notes.append(
            f"{len(masters)} non-production domain(s) pay for dedicated masters: {_names(masters)}."
        )
    return notes


def redshift(facts: FactsLike) -> list[str]:
    clusters = facts.objects.get("redshift_cluster", [])
    running = [c for c in clusters if c.state == "available" and _nonprod(c)]
    retired = [
        c
        for c in clusters
        if str((c.attributes or {}).get("node_type") or "").startswith(("ds2.", "dc1."))
    ]
    notes = []
    if running:
        notes.append(
            f"{len(running)} non-production cluster(s) run around the clock: {_names(running)}. "
            "Pause them outside working hours (storage is still billed, compute is not)."
        )
    if retired:
        notes.append(
            f"{len(retired)} cluster(s) on retired node types (move to RA3): {_names(retired)}."
        )
    return notes


def docdb_neptune(facts: FactsLike) -> list[str]:
    rows = [
        c
        for c in facts.objects.get("rds_cluster", [])
        if (c.attributes or {}).get("engine") in ("docdb", "neptune")
        and len((c.attributes or {}).get("members") or []) > 1
        and _nonprod(c)
    ]
    if not rows:
        return []
    return [f"{len(rows)} non-production cluster(s) keep read replicas: {_names(rows)}."]


STEP_TRANSITIONS_MIN = 50.0  # monthly state-transition spend before Express is worth a look


def athena(facts: FactsLike) -> list[str]:
    rows = [
        w
        for w in facts.objects.get("athena_workgroup", [])
        if w.state == "enabled" and not (w.attributes or {}).get("scan_limit_bytes")
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} Athena workgroup(s) have no per-query scan limit: {_names(rows)}. One "
        "runaway query can scan (and bill) terabytes."
    ]


def _often(schedule: str | None) -> bool:
    s = (schedule or "").lower()
    if s.startswith("rate("):
        return "minute" in s or "hour" in s
    if s.startswith("cron("):
        fields = s[5:-1].split()
        return len(fields) >= 2 and ("*" in fields[1] or "/" in fields[1] or "," in fields[1])
    return False


def glue(facts: FactsLike) -> list[str]:
    jobs = facts.objects.get("glue_job", [])
    standard = [
        j
        for j in jobs
        if (a := j.attributes or {}).get("command") == "glueetl"
        and a.get("execution_class") != "FLEX"
        and str(a.get("glue_version") or "0") >= "3.0"
    ]
    old = [j for j in jobs if str((j.attributes or {}).get("glue_version") or "0") < "2.0"]
    crawlers = [
        c
        for c in facts.objects.get("glue_crawler", [])
        if _often((c.attributes or {}).get("schedule"))
    ]
    notes = []
    if standard:
        notes.append(
            f"{len(standard)} Spark job(s) run on standard capacity; Flex costs about 34% less "
            f"for jobs that can wait: {_names(standard)}."
        )
    if old:
        notes.append(
            f"{len(old)} job(s) on Glue 0.9/1.0 are billed for at least 10 minutes a run "
            f"(1 minute from Glue 2.0): {_names(old)}."
        )
    if crawlers:
        notes.append(f"{len(crawlers)} crawler(s) run more than once a day: {_names(crawlers)}.")
    return notes


def emr(facts: FactsLike) -> list[str]:
    rows = [
        c
        for c in facts.objects.get("emr_cluster", [])
        if c.state == "waiting" and not (c.attributes or {}).get("auto_terminate")
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} EMR cluster(s) are waiting with no steps and no auto-termination: "
        f"{_names(rows)}. Set an idle timeout so they shut down on their own."
    ]


def sagemaker_notebooks(facts: FactsLike) -> list[str]:
    rows = [
        n
        for n in facts.objects.get("sagemaker_notebook", [])
        if n.state == "InService" and not (n.attributes or {}).get("lifecycle_config")
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} notebook instance(s) are running with no auto-stop (lifecycle "
        f"configuration): {_names(rows)}."
    ]


def bedrock_throughput(facts: FactsLike) -> list[str]:
    rows = [
        p
        for p in facts.objects.get("bedrock_provisioned_throughput", [])
        if p.state == "inservice" and not (p.attributes or {}).get("commitment")
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} provisioned throughput(s) are billed by the hour with no commitment: "
        f"{_names(rows)}. Delete them when not in use, or move steady use to on-demand or a "
        "commitment."
    ]


def bedrock_knowledge_bases(facts: FactsLike) -> list[str]:
    rows = [
        k
        for k in facts.objects.get("bedrock_knowledge_base", [])
        if (k.attributes or {}).get("vector_store") == "OPENSEARCH_SERVERLESS"
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} knowledge base(s) store vectors in OpenSearch Serverless, which bills a "
        f"minimum number of capacity units even when idle: {_names(rows)}. For small or test "
        "knowledge bases, Aurora PostgreSQL (pgvector) or S3 Vectors costs less."
    ]


def sqs(facts: FactsLike) -> list[str]:
    rows = [
        q
        for q in facts.objects.get("sqs_queue", [])
        if (q.attributes or {}).get("wait_seconds", 0) == 0
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} queue(s) use short polling (every empty receive is a billed request): "
        f"{_names(rows)}. Set a receive wait time of up to 20 seconds."
    ]


def transition_class(usage: str | None) -> str:
    return "transitions" if "statetransition" in (usage or "").lower() else "other"


def step_functions(facts: FactsLike) -> list[str]:
    standard = [
        m
        for m in facts.objects.get("state_machine", [])
        if (m.attributes or {}).get("type") == "STANDARD"
    ]
    costs = facts.usage_costs([m.resource_id for m in standard], transition_class)
    busy = [
        m
        for m in standard
        if costs.get(m.resource_id, {}).get("transitions", 0) >= STEP_TRANSITIONS_MIN
    ]
    if not busy:
        return []
    return [
        f"{len(busy)} Standard workflow(s) spend {STEP_TRANSITIONS_MIN:.0f}+ {facts.currency} a "
        f"month on state transitions: {_names(busy)}. Short, high-volume workflows usually "
        "cost far less as Express."
    ]


def streaming(facts: FactsLike) -> list[str]:
    streams = facts.objects.get("kinesis_stream", [])
    retention = [s for s in streams if ((s.attributes or {}).get("retention_hours") or 24) > 24]
    msk = [
        c
        for c in facts.objects.get("msk_cluster", [])
        if (c.attributes or {}).get("type") == "PROVISIONED"
        and ((c.attributes or {}).get("brokers") or 0) >= 3
        and _nonprod(c)
    ]
    mq = [
        b
        for b in facts.objects.get("mq_broker", [])
        if "MULTI_AZ" in str((b.attributes or {}).get("deployment") or "") and _nonprod(b)
    ]
    notes = []
    if retention:
        notes.append(
            f"{len(retention)} Kinesis stream(s) pay for retention beyond 24 hours: {_names(retention)}."
        )
    if msk:
        notes.append(f"{len(msk)} non-production MSK cluster(s) run 3+ brokers: {_names(msk)}.")
    if mq:
        notes.append(f"{len(mq)} non-production MQ broker(s) are Multi-AZ: {_names(mq)}.")
    return notes


BILL_REVIEW_MIN = 50.0  # monthly spend before a bill-only note is worth reading
FREE_DASHBOARDS = 3
SECRET_IDLE_DAYS = 90
WORKSPACE_IDLE_DAYS = 30


def _money(facts: FactsLike, value: float) -> str:
    return f"{value:,.2f} {facts.currency}"


def public_ipv4(facts: FactsLike) -> list[str]:
    c = facts.pattern_costs(
        {"idle": "%PublicIPv4:IdleAddress%", "in_use": "%PublicIPv4:InUseAddress%"}
    )
    notes = []
    if c["idle"] >= 1:
        notes.append(
            f"Idle public IPv4 addresses cost {_money(facts, c['idle'])} a month: release them."
        )
    if c["in_use"] >= BILL_REVIEW_MIN:
        notes.append(
            f"Public IPv4 addresses in use cost {_money(facts, c['in_use'])} a month (about 3.65 USD "
            "each); put services behind a load balancer or use IPv6 where you can."
        )
    return notes


def cloudfront(facts: FactsLike) -> list[str]:
    rows = [
        d
        for d in facts.objects.get("cloudfront_distribution", [])
        if d.state == "enabled" and (d.attributes or {}).get("price_class") == "PriceClass_All"
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} distribution(s) serve from every edge location (PriceClass_All): "
        f"{_names(rows)}. If your users are in North America and Europe, PriceClass_100 costs less."
    ]


def route53(facts: FactsLike) -> list[str]:
    rows = [
        z
        for z in facts.objects.get("route53_zone", [])
        if ((z.attributes or {}).get("records") or 0) <= 2
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} hosted zone(s) hold no records beyond NS/SOA (0.50 USD a month each): {_names(rows)}."
    ]


def transit(facts: FactsLike) -> list[str]:
    dx = [
        c
        for c in facts.objects.get("dx_connection", [])
        if c.state not in ("available", "deleted", None)
    ]
    pending = [
        a
        for a in facts.objects.get("tgw_attachment", [])
        if a.state not in ("available", "deleted")
    ]
    tgw = facts.pattern_costs({"tgw": "%TransitGateway-Hours%"})["tgw"]
    notes = []
    if dx:
        notes.append(
            f"{len(dx)} Direct Connect connection(s) are not available but still billed: {_names(dx)}."
        )
    if pending:
        notes.append(
            f"{len(pending)} Transit Gateway attachment(s) are not in use: {_names(pending)}."
        )
    if tgw >= BILL_REVIEW_MIN:
        count = len(facts.objects.get("tgw_attachment", []))
        notes.append(
            f"Transit Gateway attachments cost {_money(facts, tgw)} a month across {count} attachment(s); "
            "remove attachments for VPCs that no longer talk to others."
        )
    return notes


def logs_ingestion(facts: FactsLike) -> list[str]:
    cost = facts.pattern_costs({"ingest": "%DataProcessing-Bytes%"})["ingest"]
    if cost < BILL_REVIEW_MIN:
        return []
    groups = sorted(
        facts.objects.get("log_group", []),
        key=lambda g: -float((g.attributes or {}).get("stored_bytes") or 0),
    )[:3]
    return [
        f"Log ingestion costs {_money(facts, cost)} a month. Drop debug logs, sample noisy ones, and move "
        "logs you rarely query to the Infrequent Access log class (half the ingestion price)."
        + (f" Largest groups: {_names(groups)}." if groups else "")
    ]


def cloudwatch_metrics(facts: FactsLike) -> list[str]:
    dashboards = facts.objects.get("cw_dashboard", [])
    stuck = [a for a in facts.objects.get("cw_alarm", []) if a.state == "insufficient_data"]
    custom = facts.pattern_costs({"metrics": "%MetricMonitorUsage%"})["metrics"]
    notes = []
    if len(dashboards) > FREE_DASHBOARDS:
        notes.append(
            f"{len(dashboards)} dashboards (3 are free, then 3 USD a month each): remove the ones "
            "nobody opens."
        )
    if stuck:
        notes.append(
            f"{len(stuck)} alarm(s) have no data, often for deleted resources: {_names(stuck)}."
        )
    if custom >= BILL_REVIEW_MIN:
        notes.append(
            f"Custom metrics cost {_money(facts, custom)} a month; drop high-cardinality dimensions "
            "and metrics nobody alarms on or charts."
        )
    return notes


def xray(facts: FactsLike) -> list[str]:
    cost = facts.pattern_costs({"xray": "service:%X-Ray%"})["xray"]
    if cost < BILL_REVIEW_MIN:
        return []
    return [
        f"X-Ray costs {_money(facts, cost)} a month: lower the sampling rate for high-traffic services."
    ]


def _older_than(stamp: str, days: int) -> bool:
    from datetime import UTC, datetime, timedelta

    if not stamp:
        return True
    try:
        at = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    return at < datetime.now(UTC) - timedelta(days=days)


def kms_secrets(facts: FactsLike) -> list[str]:
    disabled = [k for k in facts.objects.get("kms_key", []) if k.state == "disabled"]
    idle = [
        s
        for s in facts.objects.get("secret", [])
        if s.state == "active"
        and _older_than((s.attributes or {}).get("last_accessed", ""), SECRET_IDLE_DAYS)
    ]
    notes = []
    if disabled:
        notes.append(
            f"{len(disabled)} disabled KMS key(s) still cost 1 USD a month each: {_names(disabled)}."
        )
    if idle:
        notes.append(
            f"{len(idle)} secret(s) not read for {SECRET_IDLE_DAYS} days (0.40 USD a month each): {_names(idle)}."
        )
    return notes


def cloudtrail(facts: FactsLike) -> list[str]:
    trails = [
        t
        for t in facts.objects.get("cloudtrail_trail", [])
        if (t.attributes or {}).get("multi_region")
    ]
    if len(trails) < 2:
        return []
    return [
        f"{len(trails)} multi-region trails record the same management events; only the first copy is "
        f"free: {_names(trails)}."
    ]


def waf(facts: FactsLike) -> list[str]:
    rows = [
        a
        for a in facts.objects.get("waf_acl", [])
        if (a.attributes or {}).get("scope") == "CLOUDFRONT"
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} CloudFront web ACL(s): check each is attached to a distribution: {_names(rows)}."
    ]


def workspaces(facts: FactsLike) -> list[str]:
    rows = [
        w
        for w in facts.objects.get("workspace", [])
        if (w.attributes or {}).get("running_mode") == "ALWAYS_ON"
        and _older_than((w.attributes or {}).get("last_connection", ""), WORKSPACE_IDLE_DAYS)
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} always-on WorkSpace(s) had no user for {WORKSPACE_IDLE_DAYS} days: {_names(rows)}. "
        "Switch them to AutoStop (billed by the hour) or remove them."
    ]


def spot_standalone(facts: FactsLike) -> list[str]:
    rows = [
        i
        for i in facts.objects.get("ec2_instance", [])
        if i.state == "running"
        and (i.attributes or {}).get("lifecycle") != "spot"
        and "aws:autoscaling:groupName" not in (i.tags or {})
        and _non_prod(i)
    ]
    if not rows:
        return []
    return [
        f"{len(rows)} standalone non-production instance(s) run On-Demand: {_names(rows)}. If they "
        "are stateless (CI runners, workers), Spot is 60 to 90% cheaper; put them in an Auto "
        "Scaling group with several instance types."
    ]


OLD_MAJORS = {"postgres": 14, "mysql": 8}


def _metrics(facts: FactsLike, ids: list[str]) -> dict[str, dict[str, dict[str, float]]]:
    from ccradar.findings.aws_usage import _usage

    db = getattr(facts, "_db", None)
    return _usage(db, ids) if db is not None and ids else {}


def rds_performance(facts: FactsLike) -> list[str]:
    from ccradar.findings.aws_usage import rds_memory_gib

    dbs = [d for d in facts.objects.get("rds_instance", []) if d.state == "available"]
    usage = _metrics(facts, [d.resource_id for d in dbs])
    crowded, pressure, queued, bursting, old = [], [], [], [], []
    for d in dbs:
        a, u = d.attributes or {}, usage.get(d.resource_id, {})
        gib = rds_memory_gib(str(a.get("instance_class") or "")) or 0
        engine = str(a.get("engine") or "")
        limit = min(gib * 2**30 / 9531392, 5000) if "postgres" in engine else gib * 2**30 / 12582880
        conns = (u.get("connections") or {}).get("max", 0)
        if limit and conns >= 0.8 * limit:
            crowded.append(d)
        free = (u.get("free_memory_bytes") or {}).get("min")
        swap = (u.get("swap_bytes") or {}).get("max", 0)
        if (free is not None and gib and free < 0.05 * gib * 2**30) or swap > 256 * 2**20:
            pressure.append(d)
        if (u.get("disk_queue") or {}).get("max", 0) > 10:
            queued.append(d)
        gb = float(a.get("storage_gb") or 0)
        peak = sum((u.get(k) or {}).get("max", 0.0) for k in ("read_iops", "write_iops"))
        if a.get("storage_type") == "gp2" and gb and gb < 1000 and peak > max(3 * gb, 100):
            bursting.append(d)
        version = str(a.get("engine_version") or "0").split(".")[0]
        family = "postgres" if "postgres" in engine else "mysql" if "mysql" in engine else None
        if family and version.isdigit() and int(version) < OLD_MAJORS[family]:
            old.append(d)
    notes = []
    if crowded:
        notes.append(
            f"{len(crowded)} database(s) reach 80%+ of their connection limit: {_names(crowded)}. "
            "Use RDS Proxy or a pooler; it often lets a smaller instance do the same work."
        )
    if pressure:
        notes.append(
            f"{len(pressure)} database(s) run short of memory or swap: {_names(pressure)}. "
            "Queries slow down; a memory-optimised class may cost less than a bigger general one."
        )
    if queued:
        notes.append(
            f"{len(queued)} database(s) queue storage requests (depth over 10): {_names(queued)}."
        )
    if bursting:
        notes.append(
            f"{len(bursting)} gp2 database(s) burst above their baseline IOPS: {_names(bursting)}. "
            "gp3 includes 3,000 IOPS at the same storage price."
        )
    if old:
        notes.append(
            f"{len(old)} database(s) run older major versions (before PostgreSQL 14 / MySQL 8): "
            f"{_names(old)}. Newer versions are faster and support Graviton and I/O improvements."
        )
    return notes


def cache_performance(facts: FactsLike) -> list[str]:
    caches = [c for c in facts.objects.get("elasticache_cluster", []) if c.state == "available"]
    usage = _metrics(facts, [c.resource_id for c in caches])
    misses, full = [], []
    for c in caches:
        u = usage.get(c.resource_id, {})
        hit = (u.get("cache_hit_rate") or {}).get("avg")
        if hit is not None and hit < 80:
            misses.append(c)
        if (u.get("evictions") or {}).get("max", 0) > 0 and (u.get("cache_memory_pct") or {}).get(
            "max", 0
        ) > 90:
            full.append(c)
    notes = []
    if full:
        notes.append(
            f"{len(full)} cache(s) are full and evicting keys: {_names(full)}. Too small for the working set."
        )
    if misses:
        notes.append(
            f"{len(misses)} cache(s) answer under 80% of reads from memory: {_names(misses)}. "
            "Check key expiry and what is cached before buying more memory."
        )
    return notes


INSPECTIONS: dict[str, Callable[[FactsLike], list[str]]] = {
    "rds_performance": rds_performance,
    "cache_performance": cache_performance,
    "spot_standalone": spot_standalone,
    "public_ipv4": public_ipv4,
    "cloudfront": cloudfront,
    "route53": route53,
    "transit": transit,
    "logs_ingestion": logs_ingestion,
    "cloudwatch_metrics": cloudwatch_metrics,
    "xray": xray,
    "kms_secrets": kms_secrets,
    "cloudtrail": cloudtrail,
    "waf": waf,
    "workspaces": workspaces,
    "athena": athena,
    "glue": glue,
    "emr": emr,
    "sagemaker_notebooks": sagemaker_notebooks,
    "bedrock_throughput": bedrock_throughput,
    "bedrock_knowledge_bases": bedrock_knowledge_bases,
    "sqs": sqs,
    "step_functions": step_functions,
    "streaming": streaming,
    "rds_licensing": rds_licensing,
    "aurora_serverless": aurora_serverless,
    "dynamodb_capacity": dynamodb_capacity,
    "elasticache_graviton": elasticache_graviton,
    "opensearch": opensearch,
    "redshift": redshift,
    "docdb_neptune": docdb_neptune,
    "s3_versions": s3_versions,
    "s3_multipart": s3_multipart,
    "glacier": glacier,
    "ebs_iops": ebs_iops,
    "efs_throughput": efs_throughput,
    "fsx": fsx,
    "backup_retention": backup_retention,
    "static_groups": static_groups,
    "fargate_spot": fargate_spot,
    "batch_environments": batch_environments,
    "ecr_policies": ecr_policies,
}
