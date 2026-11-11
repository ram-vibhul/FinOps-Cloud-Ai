from __future__ import annotations

import base64
import uuid

import pytest
from fastapi.testclient import TestClient

from ccradar.core import images
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import Organization, User
from tests.helpers import make_org, make_user, sign_in_client
from tests.test_partners import make_partner

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
EVIL_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10" onload="alert(1)">
<script>alert(2)</script>
<rect width="10" height="10" fill="#0f766e" onclick="steal()"/>
<image href="https://evil.example/x.png"/>
<foreignObject><div>hi</div></foreignObject>
<a href="javascript:alert(3)"><circle r="2" fill="url(https://evil.example/f)"/></a>
<path d="M0 0L10 10" stroke="url(#ok)"/>
</svg>"""


def test_svg_is_rebuilt_from_an_allow_list() -> None:
    kind, clean = images.check_logo(EVIL_SVG)
    text = clean.decode()
    assert kind == "image/svg+xml"
    for bad in (
        "script",
        "onload",
        "onclick",
        "evil.example",
        "foreignObject",
        "javascript",
        "<image",
        "<a ",
    ):
        assert bad not in text, bad
    assert 'fill="#0f766e"' in text and 'stroke="url(#ok)"' in text and "<path" in text
    with pytest.raises(images.ImageError, match="DOCTYPE"):
        images.check_logo(
            b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>'
        )


def test_formats_and_size() -> None:
    assert images.check_logo(PNG)[0] == "image/png"
    assert images.check_logo(b"RIFF\x00\x00\x00\x00WEBPVP8 ")[0] == "image/webp"
    for bad in (b"GIF89a....", b"%PDF-1.4", b""):
        with pytest.raises(images.ImageError):
            images.check_logo(bad)
    with pytest.raises(images.ImageError, match="too large"):
        images.check_logo(PNG + b"\x00" * images.MAX_BYTES)


def test_logo_upload_and_branding(client: TestClient, db: Database) -> None:
    pid, staff = make_partner(db)
    c = sign_in_client(client, db, staff, pid)
    bad = c.put("/api/v1/partner/logo", content=b"GIF89a", headers={"Content-Type": "image/png"})
    assert bad.json()["code"] == "bad_image"  # the bytes decide, not the content type
    ok = c.put(
        "/api/v1/partner/logo", content=EVIL_SVG, headers={"Content-Type": "image/svg+xml"}
    ).json()
    assert ok["type"] == "image/svg+xml"
    c.put(
        "/api/v1/partner/branding",
        json={"display_name": "Cloud Partners", "primary_color": "#0f766e"},
    )
    logo = c.get("/api/v1/org/branding").json()["logo"]
    assert logo.startswith("data:image/svg+xml;base64,")
    assert b"script" not in base64.b64decode(logo.split(",", 1)[1])
    assert c.delete("/api/v1/partner/logo").status_code == 204


def test_invitation_links_expire_and_work_once(client: TestClient, db: Database) -> None:
    pid, staff = make_partner(db)
    p = sign_in_client(client, db, staff, pid)
    code = p.post("/api/v1/partner/link-codes").json()["code"]
    newcomer = make_user(db, "founder@startup.io")
    n = sign_in_client(client, db, newcomer)
    assert n.get(f"/api/v1/partner-invites/{code}").json()["partner"] == "Cloud Partners Ltd"
    out = n.post(f"/api/v1/partner-invites/{code}/accept", json={"new_name": "Startup"}).json()
    org_id = uuid.UUID(out["organization_id"])
    with db.session() as s:
        set_tenant(s, bypass=True)
        org = s.get(Organization, org_id)
        assert org is not None and org.partner_id == pid
    assert n.get("/api/v1/auth/me").json()["active_organization_id"] == str(org_id)
    # Spent: the same link cannot be used again.
    assert (
        n.post(f"/api/v1/partner-invites/{code}/accept", json={"new_name": "Again"}).json()["code"]
        == "bad_code"
    )
    assert n.get(f"/api/v1/partner-invites/{code}").status_code == 404
    # Expired codes are refused.
    code2 = sign_in_client(client, db, staff, pid).post("/api/v1/partner/link-codes").json()["code"]
    with db.session() as s:
        set_tenant(s, bypass=True)
        partner = s.get(Organization, pid)
        assert partner is not None
        partner.settings = {
            **partner.settings,
            "link_codes": [
                {**c, "expires": "2000-01-01T00:00:00+00:00"}
                for c in partner.settings["link_codes"]
            ],
        }
    n = sign_in_client(client, db, newcomer, org_id)
    assert n.get(f"/api/v1/partner-invites/{code2}").status_code == 404


def test_partner_sign_up_modes(client: TestClient, db: Database, settings) -> None:
    oid, owner = make_org(db, "Reseller", "r@reseller.com")
    c = sign_in_client(client, db, owner, oid)
    body = {
        "company": "Reseller Pvt Ltd",
        "website": "https://reseller.example",
        "message": "We manage 40 AWS customers.",
    }
    assert c.post("/api/v1/org/partner-application", json=body).json() == {
        "status": "pending"
    }  # default: approval
    with db.session() as s:
        set_tenant(s, bypass=True)
        u = s.get(User, owner)
        assert u is not None
        u.is_platform_admin = True
    apps = c.get("/api/v1/platform/partner-applications").json()
    assert apps[0]["company"] == "Reseller Pvt Ltd" and apps[0]["by"] == "r@reseller.com"
    assert (
        c.post(f"/api/v1/platform/partner-applications/{oid}", json={"approve": True}).json()[
            "status"
        ]
        == "approved"
    )
    assert c.get("/api/v1/auth/me").json()["organizations"][0]["is_partner"] is True
    # Self-serve and off.
    oid2, owner2 = make_org(db, "Other", "o@other.com")
    c2 = sign_in_client(client, db, owner2, oid2)
    client.app.state.settings.partner_signup = "self_serve"  # type: ignore[attr-defined]
    assert c2.post("/api/v1/org/partner-application", json=body).json() == {"status": "approved"}
    oid3, owner3 = make_org(db, "Third", "t@third.com")
    c3 = sign_in_client(client, db, owner3, oid3)
    client.app.state.settings.partner_signup = "off"  # type: ignore[attr-defined]
    assert c3.post("/api/v1/org/partner-application", json=body).json()["code"] == "signup_off"
