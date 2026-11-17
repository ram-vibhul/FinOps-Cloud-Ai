"""Oracle Cloud, Alibaba Cloud, Hetzner, Twilio, Sentry and seat-priced connectors (11.20),
plus MongoDB Atlas detail, against fakes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, quote

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ccradar.connectors import extra, saas
from ccradar.db.base import utcnow

OCT = date(2026, 10, 1)


def this_month() -> date:
    return utcnow().date().replace(day=1)


class Fake:
    """Answers each request in turn: (status, json) or json."""

    def __init__(self, answers: list[Any], headers: list[dict[str, str]] | None = None) -> None:
        self.answers, self.headers, self.requests = answers, headers or [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        i = len(self.requests) - 1
        answer = self.answers[i] if i < len(self.answers) else self.answers[-1]
        status, body = answer if isinstance(answer, tuple) else (200, answer)
        extra_headers = self.headers[i] if i < len(self.headers) else {}
        return httpx.Response(status, json=body, headers=extra_headers)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def test_every_connector_has_a_maturity_and_a_name() -> None:
    from ccradar.costs.analytics import PROVIDER_NAMES

    assert set(extra.EXTRA) <= set(saas.CONNECTORS)
    for key, spec in saas.CONNECTORS.items():
        assert spec.maturity in ("alpha", "beta", "stable"), key
        assert key in PROVIDER_NAMES, key
    assert saas.CONNECTORS["newrelic"].maturity == "alpha"
    assert saas.CONNECTORS["oracle"].maturity == "beta"


# --- Oracle Cloud -------------------------------------------------------------------

TENANCY = "ocid1.tenancy.oc1..aaaaaaaaba3pv6wkcr4jqae5f15p2b2m2yt2j6rx32uzr4h25vqstifsfdsq"
USER = "ocid1.user.oc1..aaaaaaaa65vwl75tewwm32rgqvm6i34unq5ulcsx4h7tsuepwyfqvm6xk4oa"
FP = "20:3b:97:13:55:1c:5b:0d:d3:37:d8:50:4e:c5:3a:34"


def test_oracle_signs_requests_and_reads_daily_costs() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    page1 = {"items": [
        {"service": "Compute", "region": "ap-mumbai-1", "compartmentName": "prod",
         "skuName": "Standard - E4 - OCPU", "skuPartNumber": "B93113", "computedAmount": 12.34,
         "computedQuantity": 48, "unit": "OCPU Hours", "currency": "USD",
         "timeUsageStarted": "2026-10-02T00:00:00.000Z"},
        {"service": "Compute", "computedAmount": 0, "timeUsageStarted": "2026-10-02T00:00:00Z"},
    ]}  # fmt: skip
    page2 = {"items": [
        {"service": "Object Storage", "region": "ap-mumbai-1", "computedAmount": "1.5",
         "currency": "INR", "timeUsageStarted": "2026-10-03T00:00:00Z"},
    ]}  # fmt: skip
    fake = Fake([page1, page2], [{"opc-next-page": "p2"}, {}])
    cfg = {"tenancy_ocid": TENANCY, "user_ocid": USER, "fingerprint": FP, "region": "ap-mumbai-1"}
    lines = extra.oracle_month(fake.client(), cfg, {"private_key": pem}, OCT)
    assert [(ln.service_name, ln.billed_cost, ln.billing_currency) for ln in lines] == [
        ("Compute", Decimal("12.34"), "USD"),
        ("Object Storage", Decimal("1.5"), "INR"),
    ]
    assert lines[0].sub_account_name == "prod" and lines[0].region == "ap-mumbai-1"
    assert lines[0].charge_date == date(2026, 10, 2)
    req = fake.requests[0]
    assert str(req.url) == "https://usageapi.ap-mumbai-1.oci.oraclecloud.com/20200107/usage"
    body = json.loads(req.content)
    assert body["granularity"] == "DAILY" and body["tenantId"] == TENANCY
    assert body["timeUsageStarted"] == "2026-10-01T00:00:00Z"
    assert fake.requests[1].url.params["page"] == "p2"
    # The signature verifies with the public key over the signed headers.
    auth = req.headers["authorization"]
    assert f'keyId="{TENANCY}/{USER}/{FP}"' in auth
    signature = base64.b64decode(auth.split('signature="')[1].rstrip('"'))
    text = "\n".join(
        [
            f"date: {req.headers['date']}",
            "(request-target): post /20200107/usage",
            f"host: {req.headers['host']}",
            f"content-length: {len(req.content)}",
            "content-type: application/json",
            f"x-content-sha256: {base64.b64encode(hashlib.sha256(req.content).digest()).decode()}",
        ]
    )
    key.public_key().verify(signature, text.encode(), padding.PKCS1v15(), hashes.SHA256())


def test_oracle_rejects_bad_settings() -> None:
    cfg = {"tenancy_ocid": "x", "user_ocid": USER, "fingerprint": FP, "region": "ap-mumbai-1"}
    with pytest.raises(saas.ConnectorError):
        extra.oracle_month(Fake([{}]).client(), cfg, {"private_key": "x"}, OCT)


# --- Alibaba Cloud ------------------------------------------------------------------


def test_alibaba_daily_bills_are_signed() -> None:
    def day_answer(n: int) -> dict[str, Any]:
        return {
            "Success": True,
            "Data": {"TotalCount": 1, "PageSize": 300, "Items": {"Item": [
                {"BillingDate": f"2026-10-{n:02d}", "ProductCode": "ecs",
                 "ProductName": "Elastic Compute Service", "PretaxAmount": 3.25,
                 "Currency": "USD", "OwnerID": "1234", "SubscriptionType": "PayAsYouGo",
                 "CostUnit": "web"},
            ]}},
        }  # fmt: skip

    fake = Fake([day_answer(n) for n in range(1, 32)])
    lines = extra.alibaba_month(
        fake.client(), {}, {"access_key_id": "AKID", "access_key_secret": "s3"}, OCT
    )
    assert len(lines) == 31 and sum(ln.billed_cost for ln in lines) == Decimal("100.75")
    first = lines[0]
    assert first.service_name == "Elastic Compute Service" and first.tags == {"cost_unit": "web"}
    req = fake.requests[0]
    assert req.url.host == "business.ap-southeast-1.aliyuncs.com"
    params = dict(parse_qsl(req.url.query.decode()))
    assert params["Action"] == "QueryAccountBill" and params["BillingDate"] == "2026-10-01"
    assert params["Granularity"] == "DAILY" and params["IsGroupByProduct"] == "true"
    signature = params.pop("Signature")
    canonical = "&".join(
        f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in sorted(params.items())
    )
    text = "GET&%2F&" + quote(canonical, safe="~")
    expected = base64.b64encode(hmac.new(b"s3&", text.encode(), hashlib.sha1).digest()).decode()
    assert signature == expected


def test_alibaba_errors_are_reported() -> None:
    fake = Fake([{"Success": False, "Code": "NotAuthorized", "Message": "no"}])
    with pytest.raises(saas.ConnectorError, match="NotAuthorized"):
        extra.alibaba_month(
            fake.client(), {}, {"access_key_id": "a", "access_key_secret": "b"}, OCT
        )


# --- Hetzner ------------------------------------------------------------------------


def test_hetzner_prices_what_exists_this_month() -> None:
    month = this_month()
    created = (utcnow() - timedelta(hours=10)).isoformat()
    pricing = {"pricing": {
        "currency": "EUR",
        "volume": {"price_per_gb_month": {"net": "0.0440"}},
        "primary_ips": [{"type": "ipv4", "prices": [
            {"location": "fsn1", "price_hourly": {"net": "0.0008"}, "price_monthly": {"net": "0.5000"}}]}],
    }}  # fmt: skip
    prices = [
        {"location": "fsn1", "price_hourly": {"net": "0.0100"}, "price_monthly": {"net": "5.0000"}}
    ]
    servers = {"servers": [
        {"id": 1, "name": "web-1", "created": created, "server_type": {"name": "cx22", "prices": prices},
         "datacenter": {"location": {"name": "fsn1"}}},
    ], "meta": {"pagination": {"next_page": None}}}  # fmt: skip
    volumes = {"volumes": [{"id": 7, "name": "data", "size": 100, "created": created,
                            "location": {"name": "fsn1"}}], "meta": {}}  # fmt: skip
    lbs = {"load_balancers": [], "meta": {}}
    ips = {"primary_ips": [{"id": 3, "name": "ip", "type": "ipv4", "created": created,
                            "datacenter": {"location": {"name": "fsn1"}}}]}  # fmt: skip
    fake = Fake([pricing, servers, volumes, lbs, ips])
    lines = extra.hetzner_month(fake.client(), {}, {"token": "t"}, month)
    by = {ln.service_name: ln for ln in lines}
    assert by["Cloud Servers"].billed_cost == Decimal("0.1000")  # 10 hours x 0.01
    assert by["Cloud Servers"].billing_currency == "EUR" and by["Cloud Servers"].extras["estimate"]
    assert by["Primary IPs"].billed_cost == Decimal("0.0080")
    assert Decimal(0) < by["Volumes"].billed_cost < Decimal("4.40")
    with pytest.raises(extra.SkipMonth):
        extra.hetzner_month(fake.client(), {}, {"token": "t"}, OCT)


# --- Twilio -------------------------------------------------------------------------

SID = "AC" + "0" * 32
KEY = "SK" + "1" * 32


def test_twilio_uses_leaf_categories_and_matches_the_total() -> None:
    def rec(cat: str, price: str, day: str = "2026-10-05") -> dict[str, Any]:
        return {"category": cat, "description": cat.replace("-", " ").title(), "price": price,
                "price_unit": "usd", "start_date": day, "usage": "10", "usage_unit": "messages"}  # fmt: skip

    page1 = {"usage_records": [rec("totalprice", "10.00"), rec("sms", "6.00"),
                               rec("sms-outbound", "4.00")],
             "next_page_uri": "/2010-04-01/Accounts/x/Usage/Records/Daily.json?Page=1"}  # fmt: skip
    page2 = {"usage_records": [rec("sms-inbound", "2.00"), rec("calls", "3.50")],
             "next_page_uri": None}  # fmt: skip
    fake = Fake([page1, page2])
    lines = extra.twilio_month(
        fake.client(), {"account_sid": SID}, {"key_sid": KEY, "key_secret": "s"}, OCT
    )
    got = {ln.usage_type: ln.billed_cost for ln in lines}
    assert got == {
        "sms-outbound": Decimal("4.00"),
        "sms-inbound": Decimal("2.00"),
        "calls": Decimal("3.50"),
        "totalprice": Decimal("0.50"),  # the rest of Twilio's own total
    }
    assert sum(got.values()) == Decimal("10.00")
    assert fake.requests[0].headers["authorization"].startswith("Basic ")
    assert fake.requests[0].url.params["StartDate"] == "2026-10-01"
    assert fake.requests[1].url.path.endswith("/Usage/Records/Daily.json")


# --- Sentry -------------------------------------------------------------------------


def test_sentry_estimates_from_accepted_events() -> None:
    body = {"groups": [
        {"by": {"category": "error"}, "totals": {"sum(quantity)": 120000}},
        {"by": {"category": "transaction"}, "totals": {"sum(quantity)": 0}},
        {"by": {"category": "replay"}, "totals": {"sum(quantity)": 500}},
    ]}  # fmt: skip
    fake = Fake([body])
    cfg = {
        "org": "acme",
        "region": "de",
        "rates": "error=0.0003, replay=0.003",
        "monthly_fee": "26",
    }
    lines = extra.sentry_month(fake.client(), cfg, {"token": "t"}, OCT)
    got = {ln.service_name: ln.billed_cost for ln in lines}
    assert got == {
        "Sentry plan": Decimal(26),
        "Sentry errors": Decimal("36.0000"),
        "Sentry replays": Decimal("1.5000"),
    }
    assert fake.requests[0].url.host == "de.sentry.io"
    assert fake.requests[0].url.params["outcome"] == "accepted"
    with pytest.raises(saas.ConnectorError):
        extra.sentry_month(
            fake.client(), {"org": "acme", "rates": "error=abc"}, {"token": "t"}, OCT
        )


# --- seat-priced tools ----------------------------------------------------------------


def test_okta_counts_active_users_across_pages() -> None:
    fake = Fake(
        [[{"id": "1"}, {"id": "2"}], [{"id": "3"}]],
        [{"link": '<https://acme.okta.com/api/v1/users?after=2>; rel="next"'}, {}],
    )
    cfg = {"domain": "acme.okta.com", "price_per_user": "6"}
    (line,) = extra.okta_month(fake.client(), cfg, {"token": "t"}, this_month())
    assert (line.consumed_quantity, line.billed_cost) == (Decimal(3), Decimal("18.0000"))
    assert line.extras["estimate"] and fake.requests[0].headers["authorization"] == "SSWS t"
    with pytest.raises(extra.SkipMonth):
        extra.okta_month(fake.client(), cfg, {"token": "t"}, OCT)


def test_okta_does_not_follow_links_to_other_hosts() -> None:
    fake = Fake([[{"id": "1"}]], [{"link": '<https://evil.example/x>; rel="next"'}])
    cfg = {"domain": "acme.okta.com", "price_per_user": "6"}
    extra.okta_month(fake.client(), cfg, {"token": "t"}, this_month())
    assert len(fake.requests) == 1


def test_slack_counts_billing_active_members() -> None:
    fake = Fake([
        {"ok": True, "billable_info": {"U1": {"billing_active": True}, "U2": {"billing_active": False}},
         "response_metadata": {"next_cursor": "c2"}},
        {"ok": True, "billable_info": {"U3": {"billing_active": True}}, "response_metadata": {}},
    ])  # fmt: skip
    (line,) = extra.slack_month(
        fake.client(), {"price_per_user": "8.75"}, {"token": "t"}, this_month()
    )
    assert line.consumed_quantity == 2 and line.billed_cost == Decimal("17.5000")
    assert fake.requests[1].url.params["cursor"] == "c2"
    bad = Fake([{"ok": False, "error": "not_allowed_token_type"}])
    with pytest.raises(saas.ConnectorError, match="not_allowed_token_type"):
        extra.slack_month(bad.client(), {"price_per_user": "1"}, {"token": "t"}, this_month())


def test_zoom_counts_licensed_users() -> None:
    fake = Fake([
        {"access_token": "z"},
        {"users": [{"type": 2}, {"type": 1}, {"type": 2}], "next_page_token": ""},
    ])  # fmt: skip
    cfg = {"account_id": "acc", "client_id": "cid", "price_per_user": "13.33"}
    (line,) = extra.zoom_month(fake.client(), cfg, {"client_secret": "s"}, this_month())
    assert line.consumed_quantity == 2 and line.billed_cost == Decimal("26.6600")
    assert fake.requests[0].url.params["grant_type"] == "account_credentials"
    assert fake.requests[1].headers["authorization"] == "Bearer z"


def test_pagerduty_prices_full_users_and_stakeholders() -> None:
    fake = Fake([{"licenses": [
        {"id": "L1", "name": "Business (Full User)", "role_group": "FullUser", "current_value": 12},
        {"id": "L2", "name": "Stakeholder", "role_group": "Stakeholder", "current_value": 20},
        {"id": "L3", "name": "Unused", "role_group": "FullUser", "current_value": 0},
    ]}])  # fmt: skip
    cfg = {"price_per_user": "41", "price_per_stakeholder": "3"}
    lines = extra.pagerduty_month(fake.client(), cfg, {"token": "t"}, this_month())
    assert [(ln.consumed_quantity, ln.billed_cost) for ln in lines] == [
        (Decimal(12), Decimal("492.0000")),
        (Decimal(20), Decimal("60.0000")),
    ]
    assert fake.requests[0].headers["authorization"] == "Token token=t"


def test_atlassian_counts_jira_seats_per_product() -> None:
    fake = Fake([[
        {"key": "jira-software", "name": "Jira", "userCount": 40},
        {"key": "jira-servicedesk", "name": "Jira Service Management", "userCount": 5},
        {"key": "jira-core", "name": "Jira Work Management", "userCount": 45},
    ]])  # fmt: skip
    cfg = {"site": "acme.atlassian.net", "email": "a@acme.com", "price_per_user": "8.6",
           "prices": "jira-servicedesk=20"}  # fmt: skip
    lines = extra.atlassian_month(fake.client(), cfg, {"api_token": "t"}, this_month())
    assert {ln.usage_type: ln.billed_cost for ln in lines} == {
        "jira-software": Decimal("344.0000"),
        "jira-servicedesk": Decimal("100.0000"),
    }
    assert fake.requests[0].url.host == "acme.atlassian.net"


def test_seat_months_in_the_past_keep_what_was_imported(db) -> None:
    """The importer skips a month it cannot know, instead of emptying it."""
    from ccradar.db.tenant import set_tenant
    from ccradar.models import CloudConnection
    from tests.helpers import make_org

    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id, provider="slack", name="Slack", status="active",
            external_id=f"slack-{uuid.uuid4().hex}", secret=json.dumps({"token": "t"}),
            settings={"price_per_user": "10"},
        )  # fmt: skip
        s.add(conn)
        s.flush()
        conn_id = conn.id
    fake = Fake([{"ok": True, "billable_info": {"U1": {"billing_active": True}}}])
    with db.session() as s:
        runs = saas.import_connection(s, fake.client(), conn_id, months=2)
    assert len(runs) == 1 and len(fake.requests) == 1  # only the current month


# --- MongoDB Atlas detail ---------------------------------------------------------------


def test_atlas_lines_name_the_service_and_discount() -> None:
    assert extra is not None
    assert saas.atlas_service("ATLAS_AWS_INSTANCE_M30") == ("Atlas clusters", "Databases")
    assert (
        saas.atlas_service("ATLAS_AWS_DATA_TRANSFER_DIFFERENT_REGION")[0] == "Atlas data transfer"
    )
    assert saas.atlas_service("ATLAS_BACKUP_SNAPSHOT_STORAGE")[0] == "Atlas backup"
    assert saas.atlas_service("ATLAS_NDS_AWS_SEARCH_INSTANCE_S20")[0] == "Atlas Search"
    assert saas.atlas_cloud("ATLAS_GCP_INSTANCE_M10") == "gcp"
    org = "5" * 24
    invoice = {"lineItems": [
        {"startDate": "2026-10-04T00:00:00Z", "sku": "ATLAS_AWS_INSTANCE_M30", "clusterName": "orders",
         "groupId": "g1", "groupName": "prod", "totalPriceCents": 9000, "discountCents": 1000,
         "quantity": 24, "unit": "server hours", "tags": {"team": "payments"}},
    ]}  # fmt: skip
    fake = Fake([{"results": [{"id": "i1", "startDate": "2026-10-01"}]}, invoice, (404, {})])
    (line,) = saas.atlas_month(
        fake.client(), {"org_id": org}, {"public_key": "p", "private_key": "k"}, OCT
    )
    assert line.service_name == "Atlas clusters" and line.service_category == "Databases"
    assert (line.billed_cost, line.list_cost) == (Decimal(90), Decimal(100))
    assert line.tags == {"team": "payments", "atlas_cloud": "aws"}
    assert line.resource_name == "orders"
