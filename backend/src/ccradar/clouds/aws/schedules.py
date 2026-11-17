"""Office hours for non-production servers (11.35).

A schedule lists instances in one region and the hours they are needed. Once an
administrator approves it, the worker stops the instances at ``stop_at`` and starts them
at ``start_at`` (local time, on the chosen days) through the customer's fixes role.

Safety:
- the same guards as other fixes, checked again right before each action: not tagged
  ``ccradar-protect``, not in an Auto Scaling group, and in the expected state;
- only instances CloudCostRadar stopped are started again, so a server someone else
  stopped on purpose stays stopped;
- an action is taken at most once a day per schedule, and only within two hours of its
  time (a worker that was down overnight does not stop servers in the morning);
- turning automatic fixes off, or pausing the schedule, stops it at once.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.clouds.aws.client import AwsGateway
from ccradar.clouds.aws.fixes import PROTECT_TAG, _tags, settings_of
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CloudConnection, StopSchedule

log = logging.getLogger(__name__)
Kind = Literal["stop", "start"]
INSTANCE = re.compile(r"^i-[0-9a-f]{8,17}$")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_INSTANCES = 50
LATE = timedelta(hours=2)
LOG_KEEP = 30


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone {name!r}") from exc


def due(s: StopSchedule, now: datetime) -> Kind | None:
    """The action that is due now, if any."""
    local = now.astimezone(zone(s.timezone))
    if local.weekday() not in (s.days or []):
        return None
    state = s.state or {}
    for kind, at in (("stop", s.stop_at), ("start", s.start_at)):
        hour, minute = (int(x) for x in at.split(":"))
        moment = datetime.combine(local.date(), time(hour, minute), tzinfo=local.tzinfo)
        if (
            moment <= local < moment + LATE
            and state.get(f"last_{kind}") != local.date().isoformat()
        ):
            return kind  # type: ignore[return-value]
    return None


def _note(s: StopSchedule, entry: dict[str, Any]) -> None:
    state = dict(s.state or {})
    state["log"] = [entry, *(state.get("log") or [])][:LOG_KEEP]
    s.state = state


def run(ec2: Any, s: StopSchedule, kind: Kind, now: datetime) -> dict[str, Any]:
    """Stop or start the schedule's instances (``ec2`` is the fixes role's client)."""
    state = dict(s.state or {})
    ours: list[str] = list(state.get("stopped") or [])
    wanted = s.instance_ids if kind == "stop" else ours
    done, skipped = [], {}
    if wanted:
        found = ec2.describe_instances(InstanceIds=list(wanted)).get("Reservations") or []
        instances = {i["InstanceId"]: i for r in found for i in r.get("Instances") or []}
        for iid in wanted:
            i = instances.get(iid)
            tags = _tags((i or {}).get("Tags"))
            current = ((i or {}).get("State") or {}).get("Name")
            if i is None:
                skipped[iid] = "no longer exists"
            elif tags.get(PROTECT_TAG) == "true":
                skipped[iid] = f"tagged {PROTECT_TAG}"
            elif "aws:autoscaling:groupName" in tags:
                skipped[iid] = "belongs to an Auto Scaling group"
            elif current != ("running" if kind == "stop" else "stopped"):
                skipped[iid] = f"is {current}"
            else:
                done.append(iid)
        if done and kind == "stop":
            ec2.stop_instances(InstanceIds=done)
        elif done:
            ec2.start_instances(InstanceIds=done)
    local_day = now.astimezone(zone(s.timezone)).date().isoformat()
    state[f"last_{kind}"] = local_day
    if kind == "stop":
        state["stopped"] = sorted(set(ours) | set(done))
    else:  # whatever could not be started is not ours to retry tomorrow
        state["stopped"] = []
    s.state = state
    entry = {"at": now.isoformat(), "action": kind, "done": done, "skipped": skipped}
    _note(s, entry)
    return entry


def run_due(db: Session, gateway: AwsGateway, now: datetime | None = None) -> int:
    """Every few minutes: act on approved schedules whose time has come."""
    now = now or utcnow()
    set_tenant(db, bypass=True)
    acted = 0
    for s in list(db.scalars(select(StopSchedule).where(StopSchedule.status == "approved"))):
        try:
            kind = due(s, now)
        except ValueError:
            continue
        if kind is None:
            continue
        set_tenant(db, org_id=s.org_id)
        conn = db.get(CloudConnection, s.connection_id)
        cfg = settings_of(conn) if conn else None
        if conn is None or cfg is None:
            _note(s, {"at": now.isoformat(), "action": kind, "error": "automatic fixes are off"})
            local_day = now.astimezone(zone(s.timezone)).date().isoformat()
            s.state = {**(s.state or {}), f"last_{kind}": local_day}
            set_tenant(db, bypass=True)
            continue
        try:
            session = gateway.assume(cfg["role_arn"], conn.external_id, f"ccradar-sched-{s.id}")
            run(gateway.client(session, "ec2", region=s.region), s, kind, now)
            acted += 1
        except Exception as exc:  # any AWS error: record it and try again next time
            log.warning("schedule %s could not %s: %s", s.id, kind, type(exc).__name__)
            _note(s, {"at": now.isoformat(), "action": kind,
                      "error": f"{type(exc).__name__}: {str(exc)[:300]}"})  # fmt: skip
        db.flush()
        set_tenant(db, bypass=True)
    return acted


def validate(instance_ids: list[str], stop_at: str, start_at: str, tz: str,
             days: list[int]) -> None:  # fmt: skip
    if not instance_ids or len(instance_ids) > MAX_INSTANCES:
        raise ValueError(f"list 1 to {MAX_INSTANCES} instances")
    bad = [i for i in instance_ids if not INSTANCE.match(i)]
    if bad:
        raise ValueError(f"not instance IDs: {', '.join(bad[:5])}")
    if not HHMM.match(stop_at) or not HHMM.match(start_at) or stop_at == start_at:
        raise ValueError("use HH:MM times, and different ones for stop and start")
    if not days or any(d not in range(7) for d in days):
        raise ValueError("choose days from 0 (Monday) to 6 (Sunday)")
    zone(tz)
