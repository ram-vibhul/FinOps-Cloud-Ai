"""Live check that a billing export can be read (9.5).

Lists the export location with the connection's own read-only access and reports, in
plain words, whether CloudCostRadar can read it and whether the cloud has delivered files yet.
Nothing is downloaded. The result is kept on the connection so the page can show it."""

from __future__ import annotations

import logging
import re
from typing import Any

from ccradar.clouds.aws.client import AwsAccessError, AwsGateway
from ccradar.db.base import utcnow
from ccradar.models import CloudConnection

log = logging.getLogger(__name__)
MAX_PAGES = 5
DATA_FILE = re.compile(r"\.(parquet|csv|csv\.gz|gz)$")
AWS_PERIOD = re.compile(r"BILLING_PERIOD=(\d{4}-\d{2})")
AZURE_RANGE = re.compile(r"/(\d{8})-\d{8}/")


def _result(ok: bool, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "code": code, "message": message, "checked_at": utcnow().isoformat(), **extra}


def _aws_error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None) or {}
    return str((response.get("Error") or {}).get("Code") or type(exc).__name__)


def check_aws(conn: CloudConnection, gateway: AwsGateway) -> dict[str, Any]:
    bucket, prefix = conn.cur_bucket, (conn.cur_prefix or "").strip("/")
    if not bucket:
        return _result(
            False,
            "not_configured",
            "No billing export is set. CloudCostRadar uses Cost Explorer until you add one.",
        )
    location = f"s3://{bucket}/{prefix + '/' if prefix else ''}"
    if not conn.role_arn:
        return _result(False, "not_connected", "Finish connecting the account first.")
    try:
        session = gateway.assume(conn.role_arn, conn.external_id, f"ccradar-{conn.org_id}")
        s3 = gateway.client(session, "s3")
        files, periods, token = 0, set(), None
        for _ in range(MAX_PAGES):
            args: dict[str, Any] = {"Bucket": bucket, "Prefix": f"{prefix}/" if prefix else ""}
            if token:
                args["ContinuationToken"] = token
            page = s3.list_objects_v2(**args)
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if DATA_FILE.search(key):
                    files += 1
                    if m := AWS_PERIOD.search(key):
                        periods.add(m.group(1))
            token = page.get("NextContinuationToken")
            if not page.get("IsTruncated"):
                break
    except AwsAccessError as exc:
        return _result(False, exc.code, str(exc), location=location)
    except Exception as exc:
        code = _aws_error_code(exc)
        messages = {
            "NoSuchBucket": f"The bucket {bucket} does not exist (check the name and region).",
            "AccessDenied": (
                f"The CloudCostRadar role cannot list {location}. Re-deploy the latest template or "
                "allow s3:ListBucket and s3:GetObject on the bucket."
            ),
        }
        log.info("export check for %s failed: %s", conn.id, code)
        return _result(
            False,
            "access_denied" if code == "AccessDenied" else "unreadable",
            messages.get(code, f"CloudCostRadar could not read {location} ({code})."),
            location=location,
        )
    return _summary(location, files, sorted(periods))


def check_azure(conn: CloudConnection, client: Any) -> dict[str, Any]:
    from ccradar.clouds.azure.client import AzureError
    from ccradar.clouds.azure.costs import _blobs

    cfg = conn.settings or {}
    account, container = cfg.get("storage_account"), cfg.get("container")
    if not account or not container:
        return _result(
            False,
            "not_configured",
            "No cost export is set. CloudCostRadar uses Cost Management queries until you add one.",
        )
    prefix = (cfg.get("prefix") or "").strip("/")
    location = f"{account}/{container}/{prefix + '/' if prefix else ''}"
    try:
        blobs = _blobs(client, account, container, f"{prefix}/" if prefix else "")
    except AzureError as exc:
        reason = (
            "CloudCostRadar is not allowed to read it: give the app the "
            "Storage Blob Data Reader role."
            if exc.status in (401, 403)
            else str(exc)
        )
        return _result(
            False, "unreadable", f"Could not read {location}. {reason}", location=location
        )
    files = [name for name, _ in blobs if DATA_FILE.search(name)]
    periods = sorted(
        {f"{m.group(1)[:4]}-{m.group(1)[4:6]}" for n in files if (m := AZURE_RANGE.search(f"/{n}"))}
    )
    return _summary(location, len(files), periods)


def _summary(location: str, files: int, periods: list[str]) -> dict[str, Any]:
    if not files:
        return _result(
            True,
            "empty",
            "CloudCostRadar can read the export location, but no files have arrived yet. The cloud "
            "delivers the first export within 24 hours of creating it.",
            location=location,
            files=0,
            months=[],
        )
    months = f" covering {', '.join(periods[-3:])}" if periods else ""
    return _result(
        True,
        "ready",
        f"CloudCostRadar can read the export: {files} file{'s' if files != 1 else ''}{months}.",
        location=location,
        files=files,
        months=periods,
    )


def check(conn: CloudConnection, *, aws: AwsGateway, azure_client: Any = None) -> dict[str, Any]:
    """Run the check for ``conn`` and keep the result in its settings."""
    if conn.provider == "aws":
        result = check_aws(conn, aws)
    elif conn.provider == "azure":
        if azure_client is None:
            from ccradar.clouds.azure.client import for_connection

            azure_client = for_connection(conn)
        result = check_azure(conn, azure_client)
    else:
        return _result(
            False,
            "not_applicable",
            "This connection does not use a storage export; CloudCostRadar checks access "
            "at each import.",
        )
    conn.settings = {**(conn.settings or {}), "export_check": result}
    return result
