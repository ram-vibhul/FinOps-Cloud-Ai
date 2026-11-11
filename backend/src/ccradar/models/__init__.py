"""All ORM models. Import here so Alembic autogenerate sees them."""

from ccradar.models.accounts import (
    Identity,
    Invitation,
    Membership,
    Organization,
    OrgIdentityProvider,
    User,
    UserSession,
)
from ccradar.models.advice import ExternalRecommendation
from ccradar.models.ai_usage import AiUsageEvent, AiUsageKey
from ccradar.models.allocation import AllocationRule
from ccradar.models.anomalies import CostAnomaly
from ccradar.models.api_keys import OrgApiKey
from ccradar.models.audit import AuditEvent
from ccradar.models.aws_audit import AuditResult, AuditRun
from ccradar.models.billing import Subscription
from ccradar.models.budgets import Budget
from ccradar.models.changes import ChangeEvent, InboundWebhook
from ccradar.models.commitments import (
    Commitment,
    CommitmentSnapshot,
    Contract,
    PlannedPurchase,
    PurchasePolicy,
)
from ccradar.models.connections import CloudConnection
from ccradar.models.costs import CloudAccount, CostDaily, CostImportRun, CostLineItem
from ccradar.models.custom_roles import CustomRole
from ccradar.models.elevation import ElevatedAccess
from ccradar.models.files import FileImport
from ccradar.models.findings import Finding, FindingEvent, OwnerRule, Suppression
from ccradar.models.fixes import FixAction
from ccradar.models.fx import ExchangeRate
from ccradar.models.fx_overrides import FxOverride
from ccradar.models.governance import Policy, PolicyViolation
from ccradar.models.inbox import InboxItem
from ccradar.models.instance import InstanceSetting, RateLimitBucket
from ccradar.models.inventory import (
    CloudResource,
    CostHourly,
    NetworkAttribution,
    ResourceMetric,
)
from ccradar.models.k8s import (
    K8sAgentTask,
    K8sCluster,
    K8sCost,
    K8sEvent,
    K8sInvestigation,
    K8sMetricPoint,
    K8sMetricQuery,
    K8sObject,
    K8sUsage,
)
from ccradar.models.notifications import NotificationChannel
from ccradar.models.payments import PaymentEvent
from ccradar.models.privacy import DataExport
from ccradar.models.reliability import ReliabilityTarget
from ccradar.models.report_schedules import ReportSchedule
from ccradar.models.statements import Invoice, MonthClose
from ccradar.models.teams import OrgDomain, Team, TeamMember
from ccradar.models.unit_metrics import UnitMetric, UnitMetricValue
from ccradar.models.usage import UsageCounter, UsageSnapshot
from ccradar.models.views import SavedView
from ccradar.models.vtags import VirtualTag
from ccradar.models.webhooks_out import OutgoingWebhook, WebhookDelivery
from ccradar.models.work import FindingLink, WorkIntegration

__all__ = [
    "AiUsageEvent",
    "AiUsageKey",
    "AllocationRule",
    "AuditEvent",
    "AuditResult",
    "AuditRun",
    "Budget",
    "ChangeEvent",
    "CloudAccount",
    "CloudConnection",
    "CloudResource",
    "Commitment",
    "CommitmentSnapshot",
    "Contract",
    "CostAnomaly",
    "CostDaily",
    "CostHourly",
    "CostImportRun",
    "CostLineItem",
    "CustomRole",
    "DataExport",
    "ElevatedAccess",
    "ExchangeRate",
    "ExternalRecommendation",
    "FileImport",
    "Finding",
    "FindingEvent",
    "FindingLink",
    "FixAction",
    "FxOverride",
    "Identity",
    "InboundWebhook",
    "InboxItem",
    "InstanceSetting",
    "Invitation",
    "Invoice",
    "K8sAgentTask",
    "K8sCluster",
    "K8sCost",
    "K8sEvent",
    "K8sInvestigation",
    "K8sMetricPoint",
    "K8sMetricQuery",
    "K8sObject",
    "K8sUsage",
    "Membership",
    "MonthClose",
    "NetworkAttribution",
    "NotificationChannel",
    "OrgApiKey",
    "OrgDomain",
    "OrgIdentityProvider",
    "Organization",
    "OutgoingWebhook",
    "OwnerRule",
    "PaymentEvent",
    "PlannedPurchase",
    "Policy",
    "PolicyViolation",
    "PurchasePolicy",
    "RateLimitBucket",
    "ReliabilityTarget",
    "ReportSchedule",
    "ResourceMetric",
    "SavedView",
    "Subscription",
    "Suppression",
    "Team",
    "TeamMember",
    "UnitMetric",
    "UnitMetricValue",
    "UsageCounter",
    "UsageSnapshot",
    "User",
    "UserSession",
    "VirtualTag",
    "WebhookDelivery",
    "WorkIntegration",
]

import ccradar.audit.recorder  # noqa: F401  (installs the audit hook)
