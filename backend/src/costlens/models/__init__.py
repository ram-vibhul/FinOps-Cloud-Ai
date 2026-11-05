"""All ORM models. Import here so Alembic autogenerate sees them."""

from costlens.models.accounts import (
    Identity,
    Invitation,
    Membership,
    Organization,
    OrgIdentityProvider,
    User,
    UserSession,
)
from costlens.models.anomalies import CostAnomaly
from costlens.models.audit import AuditEvent
from costlens.models.changes import ChangeEvent, InboundWebhook
from costlens.models.connections import CloudConnection
from costlens.models.costs import CloudAccount, CostDaily, CostImportRun, CostLineItem
from costlens.models.files import FileImport
from costlens.models.fx import ExchangeRate
from costlens.models.fx_overrides import FxOverride
from costlens.models.instance import InstanceSetting, RateLimitBucket
from costlens.models.inventory import CloudResource, CostHourly
from costlens.models.k8s import K8sCluster, K8sEvent, K8sObject
from costlens.models.privacy import DataExport
from costlens.models.views import SavedView
from costlens.models.vtags import VirtualTag

__all__ = [
    "AuditEvent",
    "ChangeEvent",
    "CloudAccount",
    "CloudConnection",
    "CloudResource",
    "CostAnomaly",
    "CostDaily",
    "CostHourly",
    "CostImportRun",
    "CostLineItem",
    "DataExport",
    "ExchangeRate",
    "FileImport",
    "FxOverride",
    "Identity",
    "InboundWebhook",
    "InstanceSetting",
    "Invitation",
    "K8sCluster",
    "K8sEvent",
    "K8sObject",
    "Membership",
    "OrgIdentityProvider",
    "Organization",
    "RateLimitBucket",
    "SavedView",
    "User",
    "UserSession",
    "VirtualTag",
]

import costlens.audit.recorder  # noqa: F401  (installs the audit hook)
