"""Office-hours schedules (11.35): stop non-production servers at night, through the
fixes role, after an administrator approves; start only what we stopped."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from ccradar.clouds.aws import schedules
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import StopSchedule
from tests.fakes.aws import FakeAws
from tests.helpers import sign_in_client
from tests.test_fixes import FIX_ROLE, READ_ROLE, enable, env  # noqa: F401 - fixture

# 20:05 in Kolkata on Monday 9 November 2026 is 14:35 UTC; 08:10 on Tuesday is 02:40 UTC.
EVENING = datetime(2026, 11, 9, 14, 35, tzinfo=UTC)
MORNING = datetime(2026, 11, 10, 2, 40, tzinfo=UTC)


class Ec2:
    def __init__(self) -> None:
        def inst(iid: str, state: str, tags: dict[str, str] | None = None) -> dict[str, Any]:
            return {"InstanceId": iid, "State": {"Name": state},
                    "Tags": [{"Key": k, "Value": v} for k, v in (tags or {}).items()]}  # fmt: skip

        self.instances = {
            "i-0000000a": inst("i-0000000a", "running"),
            "i-0000000b": inst("i-0000000b", "stopped"),  # someone stopped it: not ours
            "i-0000000c": inst("i-0000000c", "running", {"ccradar-protect": "true"}),
            "i-0000000d": inst("i-0000000d", "running", {"aws:autoscaling:groupName": "web"}),
        }
        self.calls: list[tuple[str, list[str]]] = []

    def describe_instances(self, InstanceIds: list[str]) -> dict[str, Any]:
        found = [self.instances[i] for i in InstanceIds if i in self.instances]
        return {"Reservations": [{"Instances": found}]}

    def stop_instances(self, InstanceIds: list[str]) -> None:
        self.calls.append(("stop", InstanceIds))
        for i in InstanceIds:
            self.instances[i]["State"]["Name"] = "stopped"

    def start_instances(self, InstanceIds: list[str]) -> None:
        self.calls.append(("start", InstanceIds))
        for i in InstanceIds:
            self.instances[i]["State"]["Name"] = "running"


def test_request_approve_stop_and_start_only_ours(client: TestClient, db: Database, env) -> None:  # noqa: F811
    ec2 = Ec2()
    env["aws"] = FakeAws(clients={"ec2": ec2})
    env["aws"].allow(READ_ROLE, "ext")
    env["aws"].allow(FIX_ROLE, "ext")
    dev = sign_in_client(client, db, env["dev"], env["org"])
    body = {"connection_id": str(env["conn"]), "name": "Staging", "region": "ap-south-1",
            "instance_ids": list(ec2.instances), "timezone": "Asia/Kolkata",
            "days": [0, 1, 2, 3, 4], "stop_at": "20:00", "start_at": "08:00"}  # fmt: skip
    bad = dev.post("/api/v1/schedules", json={**body, "instance_ids": ["i-1; reboot"]})
    assert bad.status_code == 400 and bad.json()["code"] == "bad_schedule"
    made = dev.post("/api/v1/schedules", json=body)
    assert made.status_code == 201 and made.json()["status"] == "requested"
    sid = made.json()["id"]
    assert dev.post(f"/api/v1/schedules/{sid}/approve").status_code == 403
    owner = sign_in_client(client, db, env["owner"], env["org"])
    assert owner.post(f"/api/v1/schedules/{sid}/approve").json()["code"] == "fixes_off"
    enable(client, db, env)
    assert owner.post(f"/api/v1/schedules/{sid}/approve").json()["status"] == "approved"

    with db.session() as s:
        assert schedules.run_due(s, env["aws"], EVENING) == 1
        assert schedules.run_due(s, env["aws"], EVENING) == 0  # once a day
    assert ec2.calls == [("stop", ["i-0000000a"])]
    out = owner.get("/api/v1/schedules").json()[0]
    assert out["stopped_now"] == ["i-0000000a"]
    assert out["log"][0]["skipped"] == {
        "i-0000000b": "is stopped",
        "i-0000000c": "tagged ccradar-protect",
        "i-0000000d": "belongs to an Auto Scaling group",
    }
    with db.session() as s:
        assert schedules.run_due(s, env["aws"], MORNING) == 1
    assert ec2.calls[-1] == ("start", ["i-0000000a"])  # never i-0000000b
    assert ec2.instances["i-0000000b"]["State"]["Name"] == "stopped"


def test_due_respects_days_time_zone_and_lateness() -> None:
    s = StopSchedule(timezone="Asia/Kolkata", days=[0, 1, 2, 3, 4], stop_at="20:00",
                     start_at="08:00", state={})  # fmt: skip
    assert schedules.due(s, EVENING) == "stop"
    assert schedules.due(s, MORNING) == "start"
    late = datetime(2026, 11, 9, 17, 0, tzinfo=UTC)  # 22:30 local: more than two hours late
    assert schedules.due(s, late) is None
    saturday = datetime(2026, 11, 14, 14, 35, tzinfo=UTC)
    assert schedules.due(s, saturday) is None
    s.state = {"last_stop": "2026-11-09"}
    assert schedules.due(s, EVENING) is None


def test_paused_schedule_does_nothing(client: TestClient, db: Database, env) -> None:  # noqa: F811
    with db.session() as s:
        set_tenant(s, org_id=env["org"])
        s.add(StopSchedule(org_id=env["org"], connection_id=env["conn"], name="x",
                           region="ap-south-1", instance_ids=["i-0000000a"],
                           timezone="Asia/Kolkata", status="paused", state={}))  # fmt: skip
    with db.session() as s:
        assert schedules.run_due(s, env["aws"], EVENING) == 0
