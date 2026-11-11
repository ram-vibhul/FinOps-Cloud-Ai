from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.findings.registry import FindingIn
from ccradar.findings.service import _store, run
from ccradar.inventory.aws import Found
from ccradar.inventory.service import upsert
from ccradar.models import Finding
from tests.helpers import add_to_org, make_org, sign_in_client


def _seed(db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "111",
            [
                Found(
                    "EC2",
                    "ebs_volume",
                    "vol-sandbox",
                    "r",
                    state="available",
                    tags={"Env": "Sandbox"},
                ),
                Found(
                    "EC2", "ebs_volume", "vol-prod", "r", state="available", tags={"env": "prod"}
                ),
            ],
        )
        _store(
            s,
            org_id,
            "aws.ebs_unattached",
            "waste",
            [
                FindingIn(
                    key=rid,
                    title=rid,
                    savings_monthly=10,
                    currency="USD",
                    provider="aws",
                    account_id="111",
                    resource_id=rid,
                )
                for rid in ("vol-sandbox", "vol-prod")
            ],
        )
        _store(
            s,
            org_id,
            "aws.previous_generation",
            "rightsizing",
            [
                FindingIn(
                    key="gp3:vol-sandbox",
                    title="gp3",
                    savings_monthly=2,
                    currency="USD",
                    provider="aws",
                    account_id="111",
                    resource_id="vol-sandbox",
                )
            ],
        )
    return org_id, owner


def _status(db: Database, org_id) -> dict[str, str]:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        return {f.dedup_key: f.status for f in s.scalars(select(Finding))}


def test_scopes_reversal_and_noise(client: TestClient, db: Database) -> None:
    org_id, owner = _seed(db)
    member = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        ids = {f.dedup_key: str(f.id) for f in s.scalars(select(Finding))}
    m = sign_in_client(client, db, member, org_id)
    sandbox = ids["aws.ebs_unattached:vol-sandbox"]
    wide = m.post(
        f"/api/v1/findings/{sandbox}/dismiss", json={"reason": "not_applicable", "scope": "check"}
    )
    assert wide.status_code == 403  # admins only
    o = sign_in_client(client, db, owner, org_id)
    assert (
        o.post(
            f"/api/v1/findings/{sandbox}/dismiss", json={"reason": "not_applicable", "scope": "tag"}
        ).json()["code"]
        == "tag_required"
    )
    r = o.post(
        f"/api/v1/findings/{sandbox}/dismiss",
        json={
            "reason": "accepted_risk",
            "scope": "tag",
            "tag": "env=sandbox",
            "note": "sandbox spend is fine",
        },
    )
    assert r.json()["dismissed"] == 1
    statuses = _status(db, org_id)
    assert statuses["aws.ebs_unattached:vol-sandbox"] == "dismissed"
    assert statuses["aws.ebs_unattached:vol-prod"] == "open"
    assert (
        statuses["aws.previous_generation:gp3:vol-sandbox"] == "open"
    )  # the tag rule is for one check
    # A resource rule covers every check for that resource.
    m = sign_in_client(client, db, member, org_id)
    gp3 = ids["aws.previous_generation:gp3:vol-sandbox"]
    r2 = m.post(f"/api/v1/findings/{gp3}/dismiss", json={"reason": "planned", "scope": "resource"})
    assert (
        r2.status_code == 200
        and _status(db, org_id)["aws.previous_generation:gp3:vol-sandbox"] == "dismissed"
    )
    # The next detector run keeps them dismissed.
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        _store(
            s,
            org_id,
            "aws.ebs_unattached",
            "waste",
            [
                FindingIn(
                    key=rid,
                    title=rid,
                    savings_monthly=10,
                    currency="USD",
                    provider="aws",
                    account_id="111",
                    resource_id=rid,
                )
                for rid in ("vol-sandbox", "vol-prod")
            ],
        )
        run(s, org_id, only=["no-such-detector"])
    assert _status(db, org_id)["aws.ebs_unattached:vol-sandbox"] == "dismissed"
    rules = m.get("/api/v1/findings/suppressions").json()
    tag_rule = next(x for x in rules if x["scope"] == "tag")
    assert tag_rule["reason_label"] == "We accept this cost" and tag_rule["findings"] == 1
    noise = {n["detector"]: n for n in m.get("/api/v1/findings/noise").json()}
    assert noise["aws.ebs_unattached"]["dismissed"] == 1 and noise["aws.ebs_unattached"][
        "reasons"
    ] == {"accepted_risk": 1}
    assert (
        m.delete(f"/api/v1/findings/suppressions/{tag_rule['id']}").status_code == 403
    )  # wide rule
    o = sign_in_client(client, db, owner, org_id)
    # The sandbox volume is also covered by the resource rule, so it stays dismissed.
    assert o.delete(f"/api/v1/findings/suppressions/{tag_rule['id']}").json() == {"reopened": 0}
    resource_rule = next(x for x in rules if x["scope"] == "resource")
    assert o.delete(f"/api/v1/findings/suppressions/{resource_rule['id']}").json() == {
        "reopened": 2
    }
    statuses = _status(db, org_id)
    assert statuses["aws.ebs_unattached:vol-sandbox"] == "open"
    assert statuses["aws.previous_generation:gp3:vol-sandbox"] == "open"
