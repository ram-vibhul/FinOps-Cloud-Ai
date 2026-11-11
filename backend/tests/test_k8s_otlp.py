from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.k8s.otlp import samples
from ccradar.k8s.service import ingest_objects, issue_token
from ccradar.models import K8sCluster, K8sUsage
from tests.helpers import make_org


def attr(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def payload() -> dict:
    """What an OpenTelemetry Collector (kubeletstats -> otlphttp, encoding json) sends."""
    resource = {
        "attributes": [
            attr("k8s.namespace.name", "shop"),
            attr("k8s.pod.name", "api-6c-0"),
            attr("k8s.container.name", "api"),
            attr("k8s.node.name", "n1"),
        ]
    }
    return {
        "resourceMetrics": [
            {
                "resource": resource,
                "scopeMetrics": [
                    {
                        "scope": {"name": "kubeletstats"},
                        "metrics": [
                            {
                                "name": "container.cpu.usage",
                                "unit": "{cpu}",
                                "gauge": {
                                    "dataPoints": [
                                        {"timeUnixNano": "1700000000000000000", "asDouble": 0.1},
                                        {
                                            "timeUnixNano": "1700000060000000000",
                                            "asDouble": 0.35,
                                        },  # newest wins
                                    ]
                                },
                            },
                            {
                                "name": "container.memory.working_set",
                                "unit": "By",
                                "gauge": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": "1700000060000000000",
                                            "asInt": str(768 * 2**20),
                                        }
                                    ]
                                },
                            },
                            {
                                "name": "container.filesystem.usage",
                                "gauge": {"dataPoints": [{"asInt": "1"}]},
                            },
                        ],
                    }
                ],
            }
        ]
    }


def test_parse_kubeletstats_metrics() -> None:
    found, ignored = samples(payload())
    assert found == [
        {"namespace": "shop", "pod": "api-6c-0", "container": "api", "cpu": 0.35, "memory": 0.75}
    ]
    assert ignored == 1
    assert samples({}) == ([], 0)
    no_names = {
        "resourceMetrics": [
            {
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": "container.cpu.usage",
                                "gauge": {"dataPoints": [{"asDouble": 1}]},
                            }
                        ]
                    }
                ]
            }
        ]
    }
    assert samples(no_names) == ([], 1)


def test_otlp_endpoint(client: TestClient, db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        token = issue_token(c)
        s.add(c)
        s.flush()
        ingest_objects(
            s,
            c,
            "Pod",
            "s",
            [
                {
                    "uid": "p",
                    "name": "api-6c-0",
                    "namespace": "shop",
                    "data": {
                        "owners": [{"kind": "ReplicaSet", "name": "api-6c", "uid": "rs"}],
                        "nodeName": "n1",
                        "phase": "Running",
                        "containers": [
                            {
                                "name": "api",
                                "image": "x",
                                "resources": {"requests": {"cpu": "500m", "memory": "1Gi"}},
                            }
                        ],
                    },
                }
            ],
            final=True,
        )
    client.cookies.clear()
    url = "/api/v1/agent/otlp/v1/metrics"
    auth = {"Authorization": f"Bearer {token}"}
    r = client.post(url, json=payload(), headers=auth)
    assert r.status_code == 200
    assert r.json()["partialSuccess"]["rejectedDataPoints"] == "1"
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        row = s.scalars(select(K8sUsage)).one()
    assert (row.kind, row.name, row.container) == ("Deployment", "api", "api")
    assert row.cpu_max == 0.35 and row.memory_max == 0.75 and row.cpu_request == 0.5
    proto = client.post(
        url, content=b"\x0a\x00", headers={**auth, "Content-Type": "application/x-protobuf"}
    )
    assert proto.status_code == 415 and "encoding: json" in proto.json()["detail"]
    assert (
        client.post(url, json=payload(), headers={"Authorization": "Bearer nope"}).status_code
        == 401
    )
