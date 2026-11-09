"""A fake AwsGateway: records calls, answers like AWS would. No network."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from costlens.clouds.aws.client import AwsAccessError, Identity, account_from_arn


@dataclass
class FakeSession:
    role_arn: str
    account_id: str


@dataclass
class FakeAws:
    # role ARN -> external id that its trust policy accepts
    trusted: dict[str, str] = field(default_factory=dict)
    management_account: str | None = None
    calls: list[tuple[str, Any]] = field(default_factory=list)
    identity_override: str | None = None
    clients: dict[str, Any] = field(default_factory=dict)  # service -> fake client
    denied_regions: set[str] = field(default_factory=set)  # every call there fails

    def allow(self, role_arn: str, external_id: str) -> None:
        self.trusted[role_arn] = external_id

    def assume(self, role_arn: str, external_id: str, session_name: str) -> FakeSession:
        self.calls.append(("assume", (role_arn, external_id, session_name)))
        if self.trusted.get(role_arn) != external_id:
            raise AwsAccessError("access_denied", "AWS refused to let CostLens assume the role.")
        return FakeSession(role_arn, account_from_arn(role_arn))

    def identity(self, session: FakeSession) -> Identity:
        account = self.identity_override or session.account_id
        return Identity(account_id=account, arn=f"arn:aws:sts::{account}:assumed-role/x/y")

    def organization_master(self, session: FakeSession) -> str | None:
        return self.management_account

    def client(self, session: FakeSession, service: str, region: str | None = None) -> Any:
        self.calls.append(("client", (service, region)))
        if region in self.denied_regions:
            raise AwsAccessError("access_denied", f"{region} is disabled")
        return self.clients[service]


@dataclass
class FakeCostExplorer:
    """Answers get_cost_and_usage from ``rows``: (day, account, service, unblended, amortized)."""

    rows: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    page_size: int = 3
    requests: list[dict[str, Any]] = field(default_factory=list)

    def get_cost_and_usage(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        start, end = kwargs["TimePeriod"]["Start"], kwargs["TimePeriod"]["End"]
        days = sorted({r[0] for r in self.rows if start <= r[0] < end})
        offset = int(kwargs.get("NextPageToken") or 0)
        chunk = days[offset : offset + self.page_size]
        results = [
            {
                "TimePeriod": {"Start": d, "End": d},
                "Estimated": d == days[-1],
                "Groups": [
                    {
                        "Keys": [r[1], r[2]],
                        "Metrics": {
                            "UnblendedCost": {"Amount": r[3], "Unit": "USD"},
                            "AmortizedCost": {"Amount": r[4], "Unit": "USD"},
                        },
                    }
                    for r in self.rows
                    if r[0] == d
                ],
            }
            for d in chunk
        ]
        page: dict[str, Any] = {
            "ResultsByTime": results,
            "DimensionValueAttributes": [
                {"Value": k, "Attributes": {"description": v}} for k, v in self.names.items()
            ],
        }
        if offset + self.page_size < len(days):
            page["NextPageToken"] = str(offset + self.page_size)
        return page


@dataclass
class FakeS3:
    """In-memory S3 for list_objects_v2 (paginated) and download_fileobj."""

    objects: dict[tuple[str, str], bytes] = field(default_factory=dict)
    etags: dict[tuple[str, str], str] = field(default_factory=dict)
    page_size: int = 2
    downloads: list[str] = field(default_factory=list)

    def put(self, bucket: str, key: str, data: bytes, etag: str = "1") -> None:
        self.objects[(bucket, key)] = data
        self.etags[(bucket, key)] = etag

    def list_objects_v2(
        self, Bucket: str, Prefix: str = "", ContinuationToken: str | None = None, **_: Any
    ) -> dict[str, Any]:
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        chunk = keys[start : start + self.page_size]
        page: dict[str, Any] = {
            "Contents": [
                {"Key": k, "ETag": self.etags[(Bucket, k)], "Size": len(self.objects[(Bucket, k)])}
                for k in chunk
            ]
        }
        if start + self.page_size < len(keys):
            page.update(IsTruncated=True, NextContinuationToken=str(start + self.page_size))
        return page

    def download_fileobj(self, Bucket: str, Key: str, Fileobj: Any) -> None:
        self.downloads.append(Key)
        Fileobj.write(self.objects[(Bucket, Key)])


@dataclass
class FakeCloudTrail:
    events: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def lookup_events(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"Events": list(self.events)}
