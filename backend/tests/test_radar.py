"""Live Radar (10.1): changes are priced and alerted minutes after they happen."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.changes import cloudtrail, radar
from ccradar.changes.radar import Pricing, assess
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import ChangeEvent, CloudConnection, CloudResource, InboxItem
from tests.fakes.aws import FakeAws, FakeCloudTrail
from tests.helpers import add_to_org, make_org, sign_in_client

DB_M5_LARGE = round(2 * 0.048 * 1.78, 4)  # the estimator rounds hourly prices
INVENTORY = {
    ("ec2_instance", "i-old"): {"instance_type": "m5.large", "lifecycle": "on-demand"},
    ("ebs_volume", "vol-1"): {"size_gb": 500, "volume_type": "gp3"},
    ("rds_instance", "orders"): {"instance_class": "db.m5.large", "multi_az": False},
    ("autoscaling_group", "web"): {
        "desired": 2,
        "instance_types": ["m5.large"],
        "on_demand_pct": 100,
    },
    ("autoscaling_group", "batch"): {
        "desired": 4,
        "instance_types": ["m5.large"],
        "on_demand_pct": 0,
    },
    ("redshift_cluster", "warehouse"): {"node_type": "ra3.xlplus", "nodes": 2},
}


def known(resource_type: str, key: str) -> dict[str, Any] | None:
    return INVENTORY.get((resource_type, key))


def event(source: str, name: str, request: dict, response: dict | None = None, **extra) -> dict:
    return {
        "eventSource": f"{source}.amazonaws.com",
        "eventName": name,
        "requestParameters": request,
        "responseElements": response or {},
        **extra,
    }


def monthly(detail: dict, pricing: Pricing | None = None) -> float | None:
    impact = assess(detail, known, pricing or Pricing())
    return round(impact.monthly, 2) if impact else None


def test_new_resources_are_priced_from_the_event() -> None:
    run = event(
        "ec2",
        "RunInstances",
        {"instanceType": "m5.4xlarge", "minCount": 4, "maxCount": 4},
        {"instancesSet": {"items": [{"instanceId": f"i-{n}"} for n in range(4)]}},
    )
    impact = assess(run, known, Pricing())
    assert impact is not None and impact.label == "4 x m5.4xlarge" and impact.source == "list price"
    # 16 size units x $0.048 x 730 h + 8 GB root disk, times four
    assert round(impact.monthly, 2) == round((16 * 0.048 * 730 + 8 * 0.08) * 4, 2)
    spot = event(
        "ec2",
        "RunInstances",
        {
            "instanceType": "m5.large",
            "maxCount": 1,
            "instanceMarketOptions": {"marketType": "spot"},
        },
    )
    assert monthly(spot) == round((2 * 0.048 * 730 + 0.64) * 0.35, 2)
    assert monthly(event("ec2", "CreateVolume", {"size": "2000", "volumeType": "gp3"})) == 160.0
    assert monthly(event("ec2", "CreateNatGateway", {})) == 32.85
    assert monthly(event("ec2", "AllocateAddress", {})) == 3.65
    db = event(
        "rds",
        "CreateDBInstance",
        {
            "dBInstanceClass": "db.m5.large",
            "multiAZ": True,
            "allocatedStorage": 100,
            "storageType": "gp3",
        },
    )
    assert monthly(db) == round((DB_M5_LARGE * 730 + 100 * 0.115) * 2, 2)
    nodes = event(
        "eks",
        "CreateNodegroup",
        {"instanceTypes": ["m5.xlarge"], "scalingConfig": {"desiredSize": 3}},
    )
    assert monthly(nodes) == round(4 * 0.048 * 730 * 3, 2)
    assert monthly(event("eks", "CreateCluster", {})) == 73.0
    single = event("redshift", "CreateCluster", {"nodeType": "ra3.4xlarge"})
    assert monthly(single) == round(3.26 * 730, 2)  # 10.3: one node unless told otherwise
    assert monthly(event("redshift", "CreateCluster", {"nodeType": "dc1.mystery"})) is None


def test_deletions_and_resizes_use_the_inventory() -> None:
    gone = event(
        "ec2", "TerminateInstances", {"instancesSet": {"items": [{"instanceId": "i-old"}]}}
    )
    impact = assess(gone, known, Pricing())
    assert impact is not None and impact.monthly == pytest.approx(-2 * 0.048 * 730)
    assert impact.label == "terminated 1 x m5.large"
    unknown = event(
        "ec2", "TerminateInstances", {"instancesSet": {"items": [{"instanceId": "i-x"}]}}
    )
    assert assess(unknown, known, Pricing()) is None  # never guess
    resize = event(
        "ec2",
        "ModifyInstanceAttribute",
        {"instanceId": "i-old", "instanceType": {"value": "m5.2xlarge"}},
    )
    assert monthly(resize) == round((8 - 2) * 0.048 * 730, 2)
    grow = event("ec2", "ModifyVolume", {"volumeId": "vol-1", "size": 1000})
    assert monthly(grow) == 40.0
    assert monthly(event("ec2", "DeleteVolume", {"volumeId": "vol-1"})) == -40.0
    multi = event("rds", "ModifyDBInstance", {"dBInstanceIdentifier": "orders", "multiAZ": True})
    assert monthly(multi) == round(DB_M5_LARGE * 730, 2)
    assert monthly(event("rds", "DeleteDBInstance", {"dBInstanceIdentifier": "orders"})) == round(
        -DB_M5_LARGE * 730, 2
    )


def test_own_rates_currency_and_bad_events() -> None:
    run = event("ec2", "RunInstances", {"instanceType": "m5.large", "maxCount": 1})
    own = Pricing(currency="INR", factor=80.0, prices_usd={"m5.large": 0.05})
    impact = assess(run, known, own)
    assert impact is not None and impact.source == "bill" and impact.currency == "INR"
    assert round(impact.monthly, 2) == round((0.05 * 730 + 0.64) * 80, 2)
    refused = {**run, "errorCode": "Client.UnauthorizedOperation"}
    assert assess(refused, known, Pricing()) is None  # the call failed: nothing changed
    odd = event("ec2", "RunInstances", {"instanceType": None, "maxCount": "many"})
    assert assess(odd, known, Pricing()) is None  # odd shapes never break the import
    assert radar.short_actor("arn:aws:sts::1:assumed-role/Admin/ravi") == "ravi"
    assert radar.short_actor(None) == "Someone"


def raw(event_id: str, name: str, source: str, request: dict, minutes_ago: int = 5, **more) -> dict:
    return {
        "EventId": event_id,
        "EventName": name,
        "EventSource": f"{source}.amazonaws.com",
        "EventTime": utcnow() - timedelta(minutes=minutes_ago),
        "Username": "ravi",
        "CloudTrailEvent": json.dumps(
            {"recipientAccountId": "222222222222", "requestParameters": request, **more}
        ),
    }


@pytest.fixture
def account(client: TestClient, db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    viewer = add_to_org(db, org_id, "v@acme.com", Role.VIEWER)
    role = "arn:aws:iam::222222222222:role/CloudCostRadarReadOnly"
    trail = FakeCloudTrail()
    aws = FakeAws(clients={"cloudtrail": trail})
    aws.allow(role, "ext")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="Prod",
            status="active",
            external_id="ext",
            role_arn=role,
            account_id="222222222222",
        )
        s.add(conn)
        now = utcnow()
        s.add(
            CloudResource(
                org_id=org_id,
                provider="aws",
                service="Amazon Elastic Compute Cloud",
                resource_type="ec2_instance",
                resource_id="i-old",
                first_seen_at=now,
                last_seen_at=now,
                attributes={"instance_type": "c5.2xlarge"},
            )
        )
        s.flush()
        conn_id = conn.id
    return {
        "c": client,
        "db": db,
        "org": org_id,
        "owner": owner,
        "viewer": viewer,
        "trail": trail,
        "aws": aws,
        "conn": conn_id,
    }


def scan(account) -> int:
    with account["db"].session() as s:
        return cloudtrail.import_connection(s, account["aws"], account["conn"], hours=3)


def test_scan_prices_alerts_once_and_feeds_the_radar(account) -> None:
    db, c = account["db"], account["c"]
    account["trail"].events = [
        raw("e1", "RunInstances", "ec2", {"instanceType": "m5.4xlarge", "maxCount": 4}),
        raw("e2", "CreateNatGateway", "ec2", {}, minutes_ago=30),  # below the alert threshold
        raw(
            "e3",
            "TerminateInstances",
            "ec2",
            {"instancesSet": {"items": [{"instanceId": "i-old"}]}},
            minutes_ago=50,
        ),
        raw(
            "e4",
            "RunInstances",
            "ec2",
            {"instanceType": "p4d.24xlarge", "maxCount": 2},
            errorCode="Client.InsufficientInstanceCapacity",
        ),
        raw("e5", "UpdateAutoScalingGroup", "autoscaling", {"desiredCapacity": 10}),
    ]
    assert scan(account) == 5
    assert scan(account) == 0  # re-reading the same window adds nothing
    with db.session() as s:
        set_tenant(s, bypass=True)
        alerts = s.scalars(select(InboxItem).where(InboxItem.event == "live_change")).all()
        titles = {a.title for a in alerts}
        assert len(alerts) == 2 and len(titles) == 1  # owner and viewer, alerted once
        title = titles.pop()
        assert title.startswith("ravi added about 2,245 USD a month: 4 x m5.4xlarge")
        assert (
            alerts[0].link.startswith("/radar?change=") and "not in your bill yet" in alerts[0].text
        )

    out = sign_in_client(c, db, account["viewer"], account["org"]).get("/api/v1/radar").json()
    assert [i["title"] for i in out["items"]] == [
        "RunInstances",
        "CreateNatGateway",
        "TerminateInstances",
    ]
    first = out["items"][0]
    seen = datetime.fromisoformat(first["occurred_at"])
    assert seen.tzinfo is not None and abs(utcnow() - seen) < timedelta(
        minutes=10
    )  # UTC, not local
    assert first["actor"] == "ravi" and first["monthly"] == 2245.12 and first["in_bill"] is False
    assert first["account"] == "222222222222" and first["source"] == "list price"
    saved = round(8 * 0.0425 * 730, 2)
    assert out["added_monthly"] == round(2245.12 + 32.85, 2) and out["removed_monthly"] == -saved
    assert out["net_monthly"] == out["not_in_bill_monthly"] == round(2245.12 + 32.85 - saved, 2)
    assert out["unpriced"] == 2 and out["billed_through"] is None and out["currency"] == "USD"

    # A later scan that can no longer price the deletion keeps the first price.
    with db.session() as s:
        set_tenant(s, org_id=account["org"])
        s.delete(s.scalars(select(CloudResource)).one())
    assert scan(account) == 0
    with db.session() as s:
        set_tenant(s, org_id=account["org"])
        gone = s.scalars(select(ChangeEvent).where(ChangeEvent.title == "TerminateInstances")).one()
        assert gone.attributes["impact"]["monthly"] == -saved


def test_threshold_and_switch(account) -> None:
    db = account["db"]
    c = sign_in_client(account["c"], db, account["owner"], account["org"])
    assert c.get("/api/v1/org/alert-settings").json()["radar_min_monthly"] == 100
    c.put("/api/v1/org/alert-settings", json={"radar_min_monthly": 5000})
    account["trail"].events = [
        raw("e1", "RunInstances", "ec2", {"instanceType": "m5.4xlarge", "maxCount": 4})
    ]
    scan(account)
    c.put("/api/v1/org/alert-settings", json={"radar_alerts": False})
    account["trail"].events = [
        raw("e2", "RunInstances", "ec2", {"instanceType": "m5.24xlarge", "maxCount": 9})
    ]
    scan(account)
    with db.session() as s:
        set_tenant(s, bypass=True)
        assert s.scalars(select(InboxItem).where(InboxItem.event == "live_change")).all() == []


def test_demo_shows_the_radar(client: TestClient, db: Database) -> None:
    from ccradar.demo.seed import ensure_demo

    with db.session() as s:
        org = ensure_demo(s)
        org_id = org.id
    _, user = make_org(db, "Me", "me@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        from ccradar.models import Membership

        s.add(Membership(org_id=org_id, user_id=user, role=Role.VIEWER))
    out = sign_in_client(client, db, user, org_id).get("/api/v1/radar").json()
    assert len(out["items"]) == 5 and out["added_monthly"] > 5000 and out["removed_monthly"] < 0
    assert out["not_in_bill_monthly"] != 0  # today's changes are not billed yet
    assert any(not i["in_bill"] for i in out["items"])


def test_loaded_times_always_carry_utc(db: Database) -> None:
    # SQLite returns naive times; the API must still send a zone (ages were hours off).
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        from ccradar.models import Organization

        org = s.get(Organization, org_id)
        assert org is not None and org.created_at.tzinfo is not None
        s.expire(org)
        assert org.created_at.tzinfo is not None  # after a refresh too


class PagedTrail:
    """CloudTrail that pages its events 50 at a time, like the real API."""

    def __init__(self, events: list[dict]) -> None:
        self.events, self.calls = events, []

    def lookup_events(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        start = int(kwargs.get("NextToken") or 0)
        page: dict[str, Any] = {"Events": self.events[start : start + 50]}
        if start + 50 < len(self.events):
            page["NextToken"] = str(start + 50)
        return page


def test_busy_accounts_are_paged_and_cut_short_scans_are_flagged(account, monkeypatch) -> None:
    db, c = account["db"], account["c"]
    busy = [raw(f"n{i}", "CreateTags", "ec2", {}) for i in range(120)]  # noise first
    busy.append(raw("big", "RunInstances", "ec2", {"instanceType": "m5.4xlarge", "maxCount": 4}))
    trail = PagedTrail(busy)
    account["aws"].clients["cloudtrail"] = trail
    assert scan(account) == 1  # found on page 3 (the old scan stopped after 5 pages anyway)
    with db.session() as s:
        set_tenant(s, bypass=True)
        state = s.get(CloudConnection, account["conn"]).settings["radar_scan"]
        assert state["complete"] is True and state["events_read"] == 121
    # The next scan starts where this one ended (minus a short overlap), not 3 hours back.
    trail.calls.clear()
    scan(account)
    started = trail.calls[0]["StartTime"]
    assert utcnow() - started < timedelta(minutes=15)

    # Too many events: the scan is marked incomplete, and the next one starts from the
    # same point instead of skipping what it could not read.
    monkeypatch.setattr(cloudtrail, "MAX_PAGES", 2)
    account["aws"].clients["cloudtrail"] = PagedTrail(busy)
    scan(account)
    with db.session() as s:
        set_tenant(s, bypass=True)
        state = s.get(CloudConnection, account["conn"]).settings["radar_scan"]
        assert state["complete"] is False and "more than 100 changes" in state["problems"][0]
        through = state["through"]
    scan(account)
    with db.session() as s:
        set_tenant(s, bypass=True)
        assert s.get(CloudConnection, account["conn"]).settings["radar_scan"]["through"] == through
    out = sign_in_client(c, db, account["owner"], account["org"]).get("/api/v1/radar").json()
    assert out["scans"] == [
        {
            "connection": "Prod",
            "at": out["scans"][0]["at"],
            "complete": False,
            "problems": [out["scans"][0]["problems"][0]],
        }
    ]


def test_inventory_is_loaded_once_per_type(account) -> None:
    from sqlalchemy import event as sa_event

    statements: list[str] = []
    with account["db"].session() as s:
        set_tenant(s, org_id=account["org"])
        engine = s.get_bind()
        listener = lambda *a: statements.append(a[2])  # noqa: E731
        sa_event.listen(engine, "before_cursor_execute", listener)
        try:
            known_ = radar.inventory_lookup(s)
            for _ in range(20):
                assert known_("ec2_instance", "i-old") == {"instance_type": "c5.2xlarge"}
                assert known_("ec2_instance", "i-missing") is None
        finally:
            sa_event.remove(engine, "before_cursor_execute", listener)
    assert sum("cloud_resources" in q for q in statements) == 1


def test_list_prices_follow_the_region() -> None:
    factors = radar.region_factors()
    assert factors["us-east-1"] == 1.0 and 1.0 < factors["ap-south-1"] < 1.2
    run = event("ec2", "RunInstances", {"instanceType": "m5.large", "maxCount": 1})
    east = assess({**run, "awsRegion": "us-east-1"}, known, Pricing())
    mumbai = assess({**run, "awsRegion": "ap-south-1"}, known, Pricing())
    assert east and mumbai
    assert mumbai.monthly == pytest.approx(east.monthly * factors["ap-south-1"])
    # The organisation's own rates are already regional: never adjusted again.
    own = Pricing(prices_usd={"m5.large": 0.05})
    a = assess({**run, "awsRegion": "us-east-1"}, known, own)
    b = assess({**run, "awsRegion": "sa-east-1"}, known, own)
    assert a and b and a.source == "bill" and a.monthly == b.monthly


def test_mark_expected_survives_rescans(account) -> None:
    """11.31: the chat button opens the Radar; a writer marks the change expected and a
    later re-read of CloudTrail keeps the mark."""
    db, c = account["db"], account["c"]
    account["trail"].events = [
        raw("e1", "RunInstances", "ec2", {"instanceType": "m5.4xlarge", "maxCount": 4})
    ]
    scan(account)
    viewer = sign_in_client(c, db, account["viewer"], account["org"])
    item = viewer.get("/api/v1/radar").json()["items"][0]
    assert item["expected"] is None
    path = f"/api/v1/radar/{item['id']}/expected"
    assert viewer.put(path, json={"expected": True}).status_code == 403
    owner = sign_in_client(c, db, account["owner"], account["org"])
    marked = owner.put(path, json={"expected": True, "note": "load test"}).json()
    assert marked["expected"]["note"] == "load test"
    with db.session() as s:  # the same event read again
        cloudtrail.import_connection(s, account["aws"], account["conn"], hours=3)
    again = owner.get("/api/v1/radar").json()["items"][0]
    assert again["expected"]["by"] and again["expected"]["note"] == "load test"
    assert owner.put(path, json={"expected": False}).json()["expected"] is None


def test_chat_buttons_use_absolute_links() -> None:
    from ccradar.core.notify import Message, payload

    m = Message(
        title="t",
        text="x",
        link="/radar?change=1",
        actions=[("Mark as expected", "/radar?change=1&expected=1"), ("Bad", "javascript:x")],
    )
    slack = payload("slack", m, "https://app.example.com/")
    urls = [e["url"] for e in slack["blocks"][-1]["elements"]]
    assert urls == [
        "https://app.example.com/radar?change=1",
        "https://app.example.com/radar?change=1&expected=1",
    ]
    teams = payload("teams", m, "https://app.example.com")
    titles = [a["title"] for a in teams["attachments"][0]["content"]["actions"]]
    assert titles == ["Open in CloudCostRadar", "Mark as expected"]
    assert payload("slack", m, "")["blocks"][-1]["type"] == "section"  # no base URL: no buttons


def test_change_that_pushes_a_budget_over_warns(account) -> None:
    """11.33: a forecast within budget plus a new change's cost for the rest of the
    period crosses the amount: warn. Budgets filtered on what a change cannot be
    matched by (tags) or on another region are left alone."""
    from ccradar.models import Budget

    db = account["db"]
    with db.session() as s:
        set_tenant(s, org_id=account["org"])
        for name, filters in (
            ("Everything", {}),
            ("Team data", {"tag:team": ["data"]}),
            ("Frankfurt", {"region": ["eu-central-1"]}),
        ):
            s.add(Budget(org_id=account["org"], name=name, amount=10, currency="USD",
                         filters=filters, alert_state={}))  # fmt: skip
    account["trail"].events = [
        raw("e1", "RunInstances", "ec2", {"instanceType": "m5.4xlarge", "maxCount": 4})
    ]
    scan(account)
    with db.session() as s:
        set_tenant(s, bypass=True)
        warned = s.scalars(select(InboxItem).where(InboxItem.event == "budget_alert")).all()
        titles = {w.title for w in warned}
    assert len(titles) == 1 and titles.pop().startswith("Budget Everything will go over")
    assert "ravi" in warned[0].text and warned[0].link.startswith("/radar?change=")


def test_live_change_breaking_a_policy_alerts(account) -> None:
    """11.41: governance policies checked on the change itself, minutes after it."""
    from ccradar.models import Policy

    db = account["db"]
    with db.session() as s:
        set_tenant(s, org_id=account["org"])
        s.add(Policy(org_id=account["org"], name="No huge servers", kind="denied_instance_types",
                     params={"patterns": ["*.24xlarge"]}, severity="critical", resource_types=[],
                     enabled=True))  # fmt: skip
    account["trail"].events = [
        raw("e1", "RunInstances", "ec2", {"instanceType": "m5.24xlarge", "maxCount": 1}),
        raw("e2", "RunInstances", "ec2", {"instanceType": "t3.small", "maxCount": 1}),
    ]
    scan(account)
    with db.session() as s:
        set_tenant(s, bypass=True)
        hits = s.scalars(select(InboxItem).where(InboxItem.event == "policy_violation")).all()
        titles = {h.title for h in hits}
    assert len(titles) == 1
    assert titles.pop().startswith("ravi broke policy No huge servers")
    feed = sign_in_client(account["c"], db, account["owner"], account["org"]).get("/api/v1/radar")
    policies = {i["label"]: i["policy"] for i in feed.json()["items"]}
    assert (
        policies["1 x m5.24xlarge"][0]["detail"]
        == "m5.24xlarge matches the denied pattern *.24xlarge"
    )
    assert policies["1 x t3.small"] == []


def test_auto_scaling_and_redshift_are_priced() -> None:
    """10.3: capacity changes from the group's last known size and type; Redshift from
    node type and count (us-east-1 list prices)."""
    m5 = 0.096 * 730
    up = event(
        "autoscaling", "SetDesiredCapacity", {"autoScalingGroupName": "web", "desiredCapacity": 6}
    )
    assert monthly(up) == round(4 * m5, 2)
    floor = event(
        "autoscaling", "UpdateAutoScalingGroup", {"autoScalingGroupName": "web", "minSize": 3}
    )
    assert monthly(floor) == round(m5, 2)  # the floor rose above the current size
    spot = event(
        "autoscaling", "SetDesiredCapacity", {"autoScalingGroupName": "batch", "desiredCapacity": 2}
    )
    assert monthly(spot) == round(-2 * m5 * 0.35, 2)  # all spot
    same = event(
        "autoscaling", "UpdateAutoScalingGroup", {"autoScalingGroupName": "web", "maxSize": 10}
    )
    assert monthly(same) is None
    unknown = event(
        "autoscaling", "SetDesiredCapacity", {"autoScalingGroupName": "x", "desiredCapacity": 9}
    )
    assert monthly(unknown) is None
    create = event("redshift", "CreateCluster", {"nodeType": "ra3.4xlarge", "numberOfNodes": 2})
    assert monthly(create) == round(2 * 3.26 * 730, 2)
    resize = event(
        "redshift", "ResizeCluster", {"clusterIdentifier": "warehouse", "numberOfNodes": 4}
    )
    assert monthly(resize) == round(2 * 1.086 * 730, 2)
    paused = event("redshift", "PauseCluster", {"clusterIdentifier": "warehouse"})
    assert monthly(paused) == round(-2 * 1.086 * 730, 2)
    eks = event("eks", "CreateCluster", {"name": "prod"})
    assert monthly(eks) == round(0.10 * 730, 2)  # same event name, other service
