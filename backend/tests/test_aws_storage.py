from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from ccradar.aws_audit.service import run_audit
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.findings.aws_storage import s3_class
from ccradar.findings.service import run
from ccradar.inventory import aws_storage as st
from ccradar.inventory.aws import Found
from ccradar.inventory.service import upsert
from ccradar.models import AuditResult, Finding
from tests.helpers import make_org


class AwsError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ("USE1-TimedStorage-ByteHrs", "standard"),
        ("TimedStorage-SIA-ByteHrs", "infrequent"),
        ("TimedStorage-INT-FA-ByteHrs", "intelligent_tiering"),
        ("TimedStorage-GlacierByteHrs", "archive"),
        ("TimedStorage-GDA-ByteHrs", "archive"),
        ("EarlyDelete-GDA", "early_delete"),
        ("Retrieval-SIA", "retrieval"),
        ("Requests-GLACIER-Tier1", "retrieval"),
        ("Requests-Tier1", "requests"),
        ("DataTransfer-Out-Bytes", "transfer"),
        (None, "other"),
    ],
)
def test_s3_usage_classes(usage: str | None, expected: str) -> None:
    assert s3_class(usage) == expected


class FakeS3:
    def list_buckets(self, BucketRegion: str, MaxBuckets: int, **kw: Any) -> dict[str, Any]:
        if "ContinuationToken" not in kw:
            return {
                "Buckets": [
                    {"Name": "logs", "BucketRegion": BucketRegion},
                    {"Name": "elsewhere", "BucketRegion": "us-west-2"},
                ],
                "ContinuationToken": "2",
            }
        return {"Buckets": [{"Name": "locked", "BucketRegion": BucketRegion}]}

    def get_bucket_versioning(self, Bucket: str) -> dict[str, Any]:
        return {"Status": "Enabled"} if Bucket == "logs" else {}

    def get_bucket_lifecycle_configuration(self, Bucket: str) -> dict[str, Any]:
        if Bucket == "locked":
            raise AwsError("AccessDenied")
        return {
            "Rules": [
                {
                    "Status": "Enabled",
                    "Transitions": [{"Days": 90, "StorageClass": "GLACIER"}],
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
                },
                {"Status": "Disabled", "NoncurrentVersionExpiration": {"NoncurrentDays": 30}},
            ]
        }

    def list_bucket_intelligent_tiering_configurations(self, Bucket: str) -> dict[str, Any]:
        return {}

    def get_bucket_tagging(self, Bucket: str) -> dict[str, Any]:
        raise AwsError("NoSuchTagSet")


class FakeEfs:
    def describe_file_systems(self, **kw: Any) -> dict[str, Any]:
        return {
            "FileSystems": [
                {
                    "FileSystemId": "fs-1",
                    "FileSystemArn": "arn:efs:fs-1",
                    "Name": "home",
                    "SizeInBytes": {"Value": 300e9, "ValueInStandard": 300e9},
                    "ThroughputMode": "provisioned",
                    "LifeCycleState": "available",
                }
            ]
        }

    def describe_lifecycle_configuration(self, FileSystemId: str) -> dict[str, Any]:
        return {"LifecyclePolicies": []}


class FakeFsx:
    def describe_file_systems(self, **kw: Any) -> dict[str, Any]:
        return {
            "FileSystems": [
                {
                    "FileSystemId": "fs-9",
                    "ResourceARN": "arn:fsx:fs-9",
                    "FileSystemType": "WINDOWS",
                    "StorageCapacity": 2048,
                    "Lifecycle": "AVAILABLE",
                    "Tags": [{"Key": "env", "Value": "dev"}],
                    "WindowsConfiguration": {
                        "DeploymentType": "MULTI_AZ_1",
                        "ThroughputCapacity": 64,
                    },
                }
            ]
        }


class FakeBackup:
    def list_backup_plans(self, **kw: Any) -> dict[str, Any]:
        return {
            "BackupPlansList": [
                {"BackupPlanId": "p1", "BackupPlanArn": "arn:plan:p1", "BackupPlanName": "daily"}
            ]
        }

    def get_backup_plan(self, BackupPlanId: str) -> dict[str, Any]:
        return {
            "BackupPlan": {
                "Rules": [
                    {"RuleName": "keep", "Lifecycle": {}},
                    {"RuleName": "yearly", "Lifecycle": {"DeleteAfterDays": 2555}},
                ]
            }
        }


def test_collectors() -> None:
    buckets = {b.name: b for b in st.s3_buckets(FakeS3(), "eu-west-1")}
    assert set(buckets) == {"logs", "locked"}  # the us-west-2 bucket belongs to its own region
    logs = buckets["logs"].attributes
    assert logs["versioning"] == "Enabled" and logs["transitions"] == ["GLACIER"]
    assert logs["aborts_multipart"] and not logs["expires_noncurrent"]  # disabled rule ignored
    assert logs["intelligent_tiering_configs"] == 0 and buckets["logs"].tags == {}
    assert buckets["locked"].attributes["lifecycle_rules"] is None  # unknown, not "none"
    assert buckets["locked"].attributes["versioning"] == "Disabled"
    (efs,) = st.efs_file_systems(FakeEfs(), "r")
    assert (
        efs.attributes["transition_to_ia"] is None
        and efs.attributes["throughput_mode"] == "provisioned"
    )
    (fsx,) = st.fsx_file_systems(FakeFsx(), "r")
    assert fsx.attributes["deployment"] == "MULTI_AZ_1" and fsx.tags == {"env": "dev"}
    (plan,) = st.backup_plans(FakeBackup(), "r")
    assert [r["delete_after_days"] for r in plan.attributes["rules"]] == [None, 2555]


def _seed(db: Database):
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    s3, gib = "Amazon Simple Storage Service", 2**30
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        none = {
            "lifecycle_rules": 0,
            "transitions": [],
            "expires_noncurrent": False,
            "aborts_multipart": False,
            "intelligent_tiering_configs": 0,
        }
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    s3,
                    "s3_bucket",
                    "cold-data",
                    "us-east-1",
                    name="cold-data",
                    state="active",
                    attributes={**none, "versioning": "Enabled"},
                ),
                Found(
                    s3,
                    "s3_bucket",
                    "busy-api",
                    "us-east-1",
                    name="busy-api",
                    state="active",
                    attributes={**none, "versioning": "Disabled", "aborts_multipart": True},
                ),
                Found(
                    s3,
                    "s3_bucket",
                    "tiered",
                    "us-east-1",
                    name="tiered",
                    state="active",
                    attributes={
                        **none,
                        "transitions": ["INTELLIGENT_TIERING"],
                        "aborts_multipart": True,
                    },
                ),
                Found(
                    s3,
                    "s3_bucket",
                    "unknown",
                    "us-east-1",
                    name="unknown",
                    state="active",
                    attributes={"lifecycle_rules": None, "intelligent_tiering_configs": None},
                ),
                Found(
                    "Amazon Elastic File System",
                    "efs_file_system",
                    "arn:efs:home",
                    "us-east-1",
                    name="home",
                    state="available",
                    attributes={
                        "file_system_id": "fs-1",
                        "bytes_total": 1000e9,
                        "bytes_standard": 1000e9,
                        "transition_to_ia": None,
                        "throughput_mode": "provisioned",
                    },
                ),
                Found(
                    "Amazon Elastic File System",
                    "efs_file_system",
                    "arn:efs:ok",
                    "us-east-1",
                    name="ok",
                    state="available",
                    attributes={"bytes_standard": 1000e9, "transition_to_ia": "AFTER_30_DAYS"},
                ),
                Found(
                    "Amazon Elastic Compute Cloud",
                    "ebs_volume",
                    "vol-io",
                    "us-east-1",
                    state="in-use",
                    attributes={"volume_type": "io2", "size_gb": 100},
                ),
                Found(
                    "Amazon FSx",
                    "fsx_file_system",
                    "arn:fsx",
                    "us-east-1",
                    name="share-dev",
                    state="available",
                    attributes={"type": "WINDOWS", "deployment": "MULTI_AZ_1"},
                ),
                Found(
                    "AWS Backup",
                    "backup_plan",
                    "arn:plan",
                    "us-east-1",
                    name="daily",
                    state="active",
                    attributes={
                        "rules": [
                            {"name": "keep", "delete_after_days": None},
                            {"name": "yearly", "delete_after_days": 2555},
                        ]
                    },
                ),
            ],
        )
    today = utcnow().date()
    rows = [
        ("cold-data", "TimedStorage-ByteHrs", 4.0),
        ("cold-data", "Requests-Tier1", 0.1),
        ("busy-api", "TimedStorage-ByteHrs", 4.0),
        ("busy-api", "Requests-Tier1", 2.0),
        ("tiered", "TimedStorage-ByteHrs", 4.0),
        ("unknown", "TimedStorage-ByteHrs", 4.0),
        ("archive", "TimedStorage-GDA-ByteHrs", 1.0),
        ("archive", "Retrieval-GDA", 0.5),
        ("archive", "EarlyDelete-GDA", 0.2),
    ]
    lines = [
        CostLine(
            charge_date=today - timedelta(days=d),
            provider="aws",
            service_name=s3,
            resource_id=rid,
            usage_type=usage,
            billed_cost=Decimal(str(cost)),
            effective_cost=Decimal(str(cost)),
            billing_currency="USD",
        )
        for d in range(1, 31)
        for rid, usage, cost in rows
    ] + [
        CostLine(
            charge_date=today - timedelta(days=d),
            provider="aws",
            service_name=svc,
            resource_id=None,
            billed_cost=Decimal(1),
            effective_cost=Decimal(1),
            billing_currency="USD",
        )
        for d in range(1, 31)
        for svc in (
            "Amazon Elastic File System",
            "Amazon FSx",
            "AWS Backup",
            "Amazon Elastic Compute Cloud",
        )
    ]
    with db.session() as s:
        for month in sorted({ln.charge_date.replace(day=1) for ln in lines}):
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="t",
                source_key=str(month),
                billing_period=month,
                lines=[ln for ln in lines if ln.charge_date.replace(day=1) == month],
            )
    return org_id, gib


def test_storage_detectors_and_audit(db: Database, monkeypatch) -> None:
    from ccradar.findings import aws_compute

    monkeypatch.setattr(aws_compute, "_usd", lambda db, org_id, currency: 1.0)
    org_id, _ = _seed(db)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        run(s, org_id, only=["aws.s3_intelligent_tiering", "aws.efs_lifecycle"])
        rows = {f.dedup_key: f for f in s.scalars(select(Finding))}
        audit = run_audit(s, org_id, run_detectors=False)
        checks = {
            r.check_id: r
            for r in s.scalars(select(AuditResult).where(AuditResult.run_id == audit.id))
        }
    month = 30.4
    cold = rows["aws.s3_intelligent_tiering:cold-data"]
    assert float(cold.savings_monthly) == pytest.approx(4 * month * 0.2, abs=0.01)
    for absent in ("busy-api", "tiered", "unknown"):  # busy, already tiered, unknown config
        assert f"aws.s3_intelligent_tiering:{absent}" not in rows
    home = rows["aws.efs_lifecycle:arn:efs:home"]
    assert home.evidence["basis"] == "list price"
    assert float(home.savings_monthly) == pytest.approx(
        1000 * 0.30 * 0.30 * (1 - 0.016 / 0.30), abs=0.01
    )
    assert "aws.efs_lifecycle:arn:efs:ok" not in rows

    assert checks["s3.tiering"].status == "saving" and checks["efs.tiering"].status == "saving"
    assert checks["s3.versions"].status == "manual" and "cold-data" in checks["s3.versions"].reason
    multipart = checks["s3.multipart"]
    assert multipart.status == "manual" and "cold-data" in multipart.reason
    assert "busy-api" not in multipart.reason and "unknown" not in multipart.reason
    glacier = checks["s3.glacier"]
    assert glacier.status == "manual" and glacier.reason.startswith("Review: archive:")
    assert checks["ebs.iops"].status == "manual" and "vol-io" in checks["ebs.iops"].reason
    assert "share-dev" in checks["fsx.capacity"].reason
    backup = checks["backup.retention"]
    assert "never delete" in backup.reason and "daily/yearly" in backup.reason
