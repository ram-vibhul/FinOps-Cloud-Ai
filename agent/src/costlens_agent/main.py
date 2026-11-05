"""Agent loop: say hello, then send snapshots and new events on their intervals."""

from __future__ import annotations

import logging
import signal
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

from costlens_agent.api import CostLensApi, TokenRejected
from costlens_agent.collect import Collector, provider_of
from costlens_agent.config import ConfigError, from_env

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
    ) -> None:
        self.api = api
        self.collector = collector
        self.kubernetes_version = kubernetes_version
        self.clock = clock
        self.config: dict[str, Any] = {}
        self.next_snapshot = 0.0
        self.next_events = 0.0
        self.sent_events: dict[str, int] = {}  # uid -> count already sent

    def hello(self) -> None:
        nodes = list(self.collector.objects("Node"))
        self.config = self.api.hello(self.kubernetes_version(), provider_of(nodes))
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
        return max(1.0, min(self.next_snapshot, self.next_events) - self.clock())


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
