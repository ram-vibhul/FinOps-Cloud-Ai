"""Agent loop: say hello, then send snapshots and new events on their intervals."""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from costlens_agent import trim
from costlens_agent.api import CostLensApi, TokenRejected
from costlens_agent.collect import Collector, provider_of, usage_samples
from costlens_agent.config import ConfigError, from_env
from costlens_agent.prometheus import Prometheus, discover
from costlens_agent.prometheus import from_env as prometheus_from_env
from costlens_agent.prometheus import probe as probe_prometheus
from costlens_agent.promql import problem as promql_problem
from costlens_agent.redact import redact
from costlens_agent.tasks import TaskApi, TaskRefused, default_task_api
from costlens_agent.tasks import run as run_task

log = logging.getLogger("costlens_agent")
SNAPSHOT_KINDS = (
    "Namespace",
    "Node",
    "Pod",
    "Deployment",
    "ReplicaSet",
    "StatefulSet",
    "DaemonSet",
    "Job",
    "CronJob",
    "Service",
    "PersistentVolumeClaim",
    "HorizontalPodAutoscaler",
)


class Agent:
    def __init__(
        self,
        api: CostLensApi,
        collector: Collector,
        kubernetes_version: Callable[[], str | None],
        clock: Callable[[], float] = time.monotonic,
        task_api: TaskApi | None = None,
        prometheus: Prometheus | None = None,
    ) -> None:
        self.prometheus = prometheus
        self.prometheus_configured = prometheus is not None  # set by the operator: never replaced
        self.next_discovery = 0.0
        self.next_metrics = 0.0
        self.probe: Callable[[str], bool] = probe_prometheus
        self.usage_source = "prometheus" if prometheus else "metrics-server"
        self.task_api = task_api
        self.next_tasks = 0.0 if task_api is not None else float("inf")
        self.api = api
        self.collector = collector
        self.kubernetes_version = kubernetes_version
        self.clock = clock
        self.config: dict[str, Any] = {}
        self.next_snapshot = 0.0
        self.next_events = 0.0
        self.next_usage = 0.0
        self.wall_clock: Callable[[], str] = lambda: datetime.now(UTC).isoformat()
        self.sent_events: dict[str, int] = {}  # uid -> count already sent

    def discover_prometheus(self) -> None:
        """Find an in-cluster Prometheus when none is configured (at most once an hour)."""
        if self.prometheus_configured or self.clock() < self.next_discovery:
            return
        self.next_discovery = self.clock() + 3600
        try:
            services = [trim.to_record("Service", s) for s in self.collector.raw("Service")]
        except Exception:  # no permission or API error: keep metrics-server
            return
        url = discover(services, self.probe)
        if url and (self.prometheus is None or self.prometheus.url != url):
            log.info("using Prometheus at %s (discovered)", url)
            self.prometheus = Prometheus(url)
        elif not url and self.prometheus is not None:
            self.prometheus = None  # it went away

    def hello(self) -> None:
        nodes = list(self.collector.objects("Node"))
        self.discover_prometheus()
        self.config = self.api.hello(
            self.kubernetes_version(),
            provider_of(nodes),
            usage_source="prometheus" if self.prometheus else "metrics-server",
            prometheus_url=self.prometheus.url if self.prometheus else None,
            prometheus_discovered=bool(self.prometheus) and not self.prometheus_configured,
        )
        self.collector.configure(
            self.config.get("include_namespaces", []), self.config.get("exclude_namespaces", [])
        )

    def snapshot(self) -> dict[str, int]:
        snapshot_id = uuid.uuid4().hex
        counts = {}
        for kind in SNAPSHOT_KINDS:
            try:
                counts[kind] = self.api.snapshot(kind, snapshot_id, self.collector.objects(kind))
            except TokenRejected:
                raise
            except Exception as error:  # e.g. a resource type this cluster does not serve
                log.warning("could not send %s: %s", kind, type(error).__name__)
        return counts

    def events(self) -> int:
        current = list(self.collector.events())
        fresh = [e for e in current if self.sent_events.get(e["uid"]) != e["count"]]
        if fresh:
            self.api.events(fresh)
        self.sent_events = {e["uid"]: e["count"] for e in current}
        return len(fresh)

    def usage(self) -> int:
        """Send one round of usage samples. Without metrics-server, try again in 30 min."""
        samples = None
        if self.prometheus is not None:
            try:
                samples = self.prometheus.usage_samples(self.collector.watched)
                self.usage_source = "prometheus"
            except Exception as error:  # fall back to metrics-server this round
                log.warning(
                    "Prometheus query failed (%s); using metrics-server", type(error).__name__
                )
                self.usage_source = "metrics-server"
        try:
            if samples is None:
                samples = usage_samples(self.collector)
        except Exception as error:
            log.info(
                "metrics-server not available (%s); usage history paused", type(error).__name__
            )
            self.next_usage = self.clock() + 1800
            return 0
        if samples:
            self.api.usage(self.wall_clock(), samples)
        return len(samples)

    def metrics(self) -> int:
        """Run the customer's own PromQL queries (validated again here, at most 10)."""
        queries = self.config.get("metric_queries") or []
        if not queries or self.prometheus is None:
            return 0
        sent = 0
        for q in queries[:10]:
            reason = promql_problem(q.get("promql") or "")
            if reason:
                self.api.metrics(q["id"], ok=False, error=f"Refused by the agent: {reason}")
                continue
            try:
                pairs = self.prometheus.instant(q["promql"])
            except TokenRejected:
                raise
            except Exception as error:
                self.api.metrics(
                    q["id"], ok=False, error=redact(f"{type(error).__name__}: {str(error)[:300]}")
                )
                continue
            self.api.metrics(
                q["id"], ok=True, series=[{"labels": labels, "value": v} for labels, v in pairs]
            )
            sent += 1
        return sent

    def tasks(self) -> int:
        """Run read-only tasks requested from CostLens (logs, describe)."""
        if self.task_api is None:
            return 0
        done = 0
        for task in self.api.claim_tasks():
            try:
                text = run_task(task, self.task_api, self.collector.watched, self.prometheus)
            except TokenRejected:
                raise
            except TaskRefused as refused:
                self.api.task_result(task["id"], ok=False, error=f"Refused by the agent: {refused}")
            except Exception as error:  # e.g. pod gone, container not started yet
                self.api.task_result(
                    task["id"], ok=False, error=f"{type(error).__name__}: {str(error)[:300]}"
                )
            else:
                self.api.task_result(task["id"], ok=True, text=text)
                done += 1
        return done

    def tick(self) -> float:
        """Run what is due; return seconds until the next task."""
        now = self.clock()
        if now >= self.next_snapshot:
            self.hello()  # also refreshes settings (watched namespaces, intervals)
            self.snapshot()
            self.next_snapshot = now + float(self.config.get("snapshot_interval_seconds", 300))
        if now >= self.next_events:
            self.events()
            self.next_events = now + float(self.config.get("events_interval_seconds", 60))
        if now >= self.next_usage:
            self.next_usage = now + float(self.config.get("usage_interval_seconds", 60))
            self.usage()  # may push next_usage further out when metrics are missing
        if now >= self.next_metrics and self.config.get("metric_queries"):
            self.next_metrics = now + float(self.config.get("metric_interval_seconds", 300))
            self.metrics()
        if now >= self.next_tasks:
            self.next_tasks = now + float(self.config.get("tasks_interval_seconds", 10))
            self.tasks()
        due = min(self.next_snapshot, self.next_events, self.next_usage, self.next_tasks)
        return max(1.0, due - self.clock())


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = from_env()
    except (ConfigError, OSError) as error:
        log.error("%s", error)
        return 2
    from kubernetes import client
    from kubernetes import config as kube_config

    kube_config.load_incluster_config()
    api_client = client.ApiClient()
    from costlens_agent.collect import default_listers

    agent = Agent(
        CostLensApi(config),
        Collector(default_listers(api_client), api_client.sanitize_for_serialization),
        lambda: client.VersionApi(api_client).get_code().git_version,
        task_api=default_task_api(api_client),
        prometheus=prometheus_from_env(dict(os.environ)),
    )
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    log.info("CostLens agent started for %s", config.url)
    while not stopping:
        try:
            wait = agent.tick()
        except TokenRejected as error:
            log.error("%s Install the new token and restart the agent.", error)
            return 3
        except Exception as error:
            log.warning("cycle failed, retrying in 60 s: %s", type(error).__name__)
            wait = 60.0
        end = time.monotonic() + wait
        while not stopping and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
