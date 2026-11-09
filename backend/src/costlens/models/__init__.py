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
from costlens.models.advice import ExternalRecommendation
from costlens.models.ai_usage import AiUsageEvent, AiUsageKey
from costlens.models.allocation import AllocationRule
from costlens.models.anomalies import CostAnomaly
from costlens.models.api_keys import OrgApiKey
from costlens.models.audit import AuditEvent
from costlens.models.aws_audit import AuditResult, AuditRun
from costlens.models.billing import Subscription
from costlens.models.budgets import Budget
from costlens.models.changes import ChangeEvent, InboundWebhook
from costlens.models.commitments import (
    Commitment,
    CommitmentSnapshot,
    Contract,
    PlannedPurchase,
    PurchasePolicy,
)
from costlens.models.connections import CloudConnection
from costlens.models.costs import CloudAccount, CostDaily, CostImportRun, CostLineItem
from costlens.models.custom_roles import CustomRole
from costlens.models.elevation import ElevatedAccess
from costlens.models.files import FileImport
from costlens.models.findings import Finding, FindingEvent, OwnerRule, Suppression
from costlens.models.fixes import FixAction
from costlens.models.fx import ExchangeRate
from costlens.models.fx_overrides import FxOverride
from costlens.models.governance import Policy, PolicyViolation
from costlens.models.inbox import InboxItem
from costlens.models.instance import InstanceSetting, RateLimitBucket
from costlens.models.inventory import (
    CloudResource,
    CostHourly,
    NetworkAttribution,
    ResourceMetric,
)
from costlens.models.k8s import (
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
from costlens.models.notifications import NotificationChannel
from costlens.models.payments import PaymentEvent
from costlens.models.privacy import DataExport
from costlens.models.reliability import ReliabilityTarget
from costlens.models.report_schedules import ReportSchedule
from costlens.models.statements import Invoice, MonthClose
from costlens.models.teams import OrgDomain, Team, TeamMember
from costlens.models.unit_metrics import UnitMetric, UnitMetricValue
from costlens.models.usage import UsageCounter, UsageSnapshot
from costlens.models.views import SavedView
from costlens.models.vtags import VirtualTag
from costlens.models.webhooks_out import OutgoingWebhook, WebhookDelivery
from costlens.models.work import FindingLink, WorkIntegration

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

import costlens.audit.recorder  # noqa: F401  (installs the audit hook)
