"""Exactly which read-only roles CloudCostRadar needs on Azure and Google Cloud, as commands to
copy, filled in with the customer's own scope, project and table where known.

Every role here is a read-only built-in role. The one exception worth naming is
BigQuery Job User: it lets the service account *run* queries (which BigQuery bills to
that project) but not change any data."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

SA_NAME = "ccradar-reader"


@dataclass(frozen=True)
class Role:
    role: str
    scope: str
    why: str


def _q(value: str) -> str:
    """Quote for a POSIX shell (single quotes, closed and reopened around any quote)."""
    return "'" + value.replace("'", "'\\''") + "'"


def azure(
    scope: str | None = None,
    storage_account: str | None = None,
    container: str | None = None,
    resource_group: str | None = None,
) -> dict[str, Any]:
    scope = scope or "/subscriptions/<subscription-id>"
    subscription = (
        scope.split("/")[2] if scope.startswith("/subscriptions/") else "<subscription-id>"
    )
    roles = [
        Role("Cost Management Reader", scope, "Read daily costs (Cost Management Query API)."),
        Role(
            "Reader",
            f"/subscriptions/{subscription}",
            "Read Azure Advisor recommendations for this subscription.",
        ),
    ]
    commands = [
        "# 1. App registration with Cost Management Reader on the scope (prints appId, password and tenant)",
        f"az ad sp create-for-rbac --name {SA_NAME} --role 'Cost Management Reader' --scopes {_q(scope)}",
        "# 2. Reader on each subscription for Azure Advisor",
        f"az role assignment create --assignee <appId> --role Reader --scope {_q('/subscriptions/' + subscription)}",
    ]
    if storage_account and container:
        rg = resource_group or "<resource-group>"
        container_scope = (
            f"/subscriptions/{subscription}/resourceGroups/{rg}/providers/Microsoft.Storage/"
            f"storageAccounts/{storage_account}/blobServices/default/containers/{container}"
        )
        roles.append(
            Role(
                "Storage Blob Data Reader",
                container_scope,
                "Read the FOCUS cost export files (this container only).",
            )
        )
        commands += [
            "# 3. Read the FOCUS export container only",
            f"az role assignment create --assignee <appId> --role 'Storage Blob Data Reader' --scope {_q(container_scope)}",
        ]
    commands.append(
        "# Paste the appId (client ID), password (client secret) and tenant into CloudCostRadar."
    )
    return {"provider": "azure", "roles": [asdict(r) for r in roles], "commands": commands}


def gcp(
    table: str | None = None,
    query_project: str | None = None,
    recommender_projects: list[str] | None = None,
) -> dict[str, Any]:
    table = table or "<billing-project>.<dataset>.<table>"
    parts = table.split(".")
    billing_project, dataset = (
        (parts[0], parts[1]) if len(parts) == 3 else ("<billing-project>", "<dataset>")
    )
    project = query_project or billing_project
    email = f"{SA_NAME}@{project}.iam.gserviceaccount.com"
    member = f"serviceAccount:{email}"
    roles = [
        Role(
            "roles/bigquery.dataViewer",
            f"dataset {billing_project}:{dataset}",
            "Read the billing export table (this dataset only).",
        ),
        Role(
            "roles/bigquery.jobUser",
            f"project {project}",
            "Run the read-only billing queries (BigQuery bills the bytes read to this project).",
        ),
        Role(
            "roles/recommender.computeViewer",
            "each project with Compute Engine spend",
            "Read idle-resource and machine-type recommendations.",
        ),
        Role(
            "roles/recommender.projectCudViewer",
            "each project with Compute Engine spend",
            "Read commitment (CUD) recommendations.",
        ),
    ]
    commands = [
        f"gcloud iam service-accounts create {SA_NAME} --project {_q(project)} --display-name 'CloudCostRadar (read-only)'",
        f"bq add-iam-policy-binding --member={_q(member)} --role=roles/bigquery.dataViewer {_q(billing_project + ':' + dataset)}",
        f"gcloud projects add-iam-policy-binding {_q(project)} --member={_q(member)} --role=roles/bigquery.jobUser",
    ]
    for p in recommender_projects or ["<project-id>"]:
        for role in ("roles/recommender.computeViewer", "roles/recommender.projectCudViewer"):
            commands.append(
                f"gcloud projects add-iam-policy-binding {_q(p)} --member={_q(member)} --role={role}"
            )
    commands += [
        f"gcloud iam service-accounts keys create ccradar-key.json --iam-account {_q(email)}",
        "# Upload ccradar-key.json in CloudCostRadar, then delete the local copy.",
    ]
    return {"provider": "gcp", "roles": [asdict(r) for r in roles], "commands": commands}


SAFE = re.compile(r"^[\w.:/@+\-<> ]*$")


def safe(value: str | None) -> str | None:
    """Keep values that will be shown inside commands boring (no shell tricks)."""
    if value is None:
        return None
    return value if SAFE.match(value) else None
