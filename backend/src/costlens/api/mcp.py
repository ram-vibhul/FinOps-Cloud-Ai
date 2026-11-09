"""Read-only MCP server for AI assistants (8.7).

Model Context Protocol over Streamable HTTP (JSON-RPC 2.0, single JSON responses) at
``POST /mcp``. Authenticate with an organisation API key (``Authorization: Bearer
clk_…``, 8.1); the key's organisation and role apply, and every tool here only reads.
Supported methods: ``initialize``, ``notifications/initialized``, ``ping``,
``tools/list``, ``tools/call``. The one exception to read-only: keys with "may start
investigations" (8.14) also get ``create_investigation``.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from costlens import __version__
from costlens.accounts.roles import Permission, has_permission
from costlens.api.deps import SessionDep, get_current_auth, plan_feature
from costlens.auth.web import AuthContext
from costlens.costs import analytics, explorer
from costlens.db.base import utcnow
from costlens.models import CostAnomaly, Finding, Organization

router = APIRouter(tags=["mcp"])
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "CostLens: cloud, Kubernetes and AI cost data for one organisation. Amounts are in the "
    "organisation's currency unless a tool says otherwise. Everything here is read-only, "
    "except create_investigation when the key allows it."
)
DIMENSIONS = [
    "service",
    "account",
    "region",
    "team",
    "project",
    "usage_type",
    "charge_category",
    "pricing_category",
]

TOOLS: list[dict[str, Any]] = [
    {
        "name": "cost_summary",
        "description": "Month-to-date spend, the month-end forecast and last month's total.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "explore_costs",
        "description": "Spend grouped by one or two dimensions (service, account, region, team, project, usage_type, tag:<key>) over the last N days, optionally filtered.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 2,
                    "default": ["service"],
                },
                "days": {"type": "integer", "minimum": 1, "maximum": 400, "default": 30},
                "filters": {
                    "type": "object",
                    "additionalProperties": {"type": "array", "items": {"type": "string"}},
                },
                "granularity": {
                    "type": "string",
                    "enum": ["total", "day", "month"],
                    "default": "total",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "top_cost_changes",
        "description": "What grew or shrank most: the last N days against the N days before, by a dimension.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "enum": ["service", "account", "region", "team", "project"],
                    "default": "service",
                },
                "days": {"type": "integer", "minimum": 1, "maximum": 90, "default": 7},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_savings",
        "description": "Open savings opportunities, largest first, with steps to take.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "category": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_anomalies",
        "description": "Unusual cost increases found in the last N days.",
        "inputSchema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 90, "default": 14}},
            "additionalProperties": False,
        },
    },
    {
        "name": "budgets",
        "description": "Every budget with spend so far and the forecast for its period.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


INVESTIGATE_TOOL: dict[str, Any] = {
    "name": "create_investigation",
    "description": (
        "Start a Kubernetes investigation of a workload: gathers recent events, restarts, metrics and "
        "changes, ranks likely causes and saves it in CostLens. The only tool that writes anything; "
        "only offered when the API key allows it."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "cluster": {"type": "string", "description": "Cluster name or id"},
            "namespace": {"type": "string"},
            "kind": {"type": "string", "default": "Deployment"},
            "name": {"type": "string"},
            "hours": {"type": "integer", "minimum": 1, "maximum": 72, "default": 6},
        },
        "required": ["cluster", "namespace", "name"],
        "additionalProperties": False,
    },
}


class ToolError(Exception):
    pass


def _rpc(id_: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    return body


def _org(db: SessionDep, auth: AuthContext) -> Organization:
    org = db.get(Organization, auth.org_id)
    if org is None:
        raise ToolError("No organisation.")
    return org


def _call(  # noqa: PLR0911 - one branch per tool
    name: str, args: dict[str, Any], db: SessionDep, auth: AuthContext
) -> Any:
    org = _org(db, auth)
    cur = org.default_currency
    today = utcnow().date()
    end = analytics.latest_date(db, org.id) or today
    if name == "cost_summary":
        s = analytics.summary(db, org.id, cur, "effective", today)
        return {
            "currency": cur,
            "month_to_date": s["month_to_date"],
            "forecast": s["forecast"]["amount"],
            "last_month": s["last_month"],
            "data_through": s.get("data_through"),
        }
    if name == "explore_costs":
        days = int(args.get("days", 30))
        try:
            q = explorer.ExploreQuery(
                start=end - timedelta(days=days - 1),
                end=end,
                group_by=args.get("group_by") or ["service"],
                filters=args.get("filters") or {},
                granularity=args.get("granularity", "total"),
                limit=50,
            )
        except ValueError as exc:
            raise ToolError(f"Invalid query: {exc}") from exc
        out = explorer.explore(db, org.id, q)
        return {
            "currency": out.get("currency"),
            "start": q.start.isoformat(),
            "end": q.end.isoformat(),
            "total": out.get("total"),
            "groups": [
                {
                    "key": g["label"],
                    "cost": g["total"],
                    "share_pct": g.get("share"),
                    "change_pct": g.get("change_pct"),
                }
                for g in out.get("groups", [])[:50]
            ],
        }
    if name == "top_cost_changes":
        m = analytics.movers(
            db,
            org.id,
            cur,
            "effective",
            args.get("dimension", "service"),
            end,
            int(args.get("days", 7)),
            10,
        )
        return {
            "currency": cur,
            "current_period": m["current_period"],
            "previous_period": m["previous_period"],
            "items": m["items"],
        }
    if name == "list_savings":
        q = (
            select(Finding)
            .where(Finding.status.in_(("open", "approved")))
            .order_by(Finding.savings_monthly.desc())
            .limit(int(args.get("limit", 20)))
        )
        if args.get("category"):
            q = q.where(Finding.category == args["category"])
        return [
            {
                "title": f.title,
                "savings_monthly": float(f.savings_monthly),
                "currency": f.currency,
                "category": f.category,
                "risk": f.risk,
                "effort": f.effort,
                "resource": f.resource_id,
                "steps": f.steps or [],
            }
            for f in db.scalars(q)
        ]
    if name == "list_anomalies":
        since: date = today - timedelta(days=int(args.get("days", 14)))
        rows = db.scalars(
            select(CostAnomaly)
            .where(CostAnomaly.last_day >= since)
            .order_by(CostAnomaly.impact.desc())
            .limit(50)
        )
        return [
            {
                "day": a.day.isoformat(),
                "what": a.key,
                "by": a.dimension,
                "actual": a.actual,
                "expected": a.expected,
                "extra": a.impact,
                "currency": a.currency,
                "status": a.status,
            }
            for a in rows
        ]
    if name == "budgets":
        from costlens.costs.budgets import all_status

        return [
            {
                k: b[k]
                for k in (
                    "name",
                    "amount",
                    "currency",
                    "period",
                    "spent",
                    "forecast",
                    "spent_pct",
                    "forecast_pct",
                )
            }
            for b in all_status(db, org.id, today)
        ]
    if name == "create_investigation" and auth.api_key_can_investigate:
        return _investigate(args, db, auth)
    raise ToolError(f"Unknown tool: {name}")


def _investigate(args: dict[str, Any], db: SessionDep, auth: AuthContext) -> dict[str, Any]:
    import uuid as _uuid

    from costlens.api.k8s_investigations import InvestigationIn, _run
    from costlens.models import K8sCluster, K8sInvestigation

    ref = str(args.get("cluster", ""))
    cluster = None
    try:
        cluster = db.get(K8sCluster, _uuid.UUID(ref))
    except ValueError:
        cluster = db.scalar(select(K8sCluster).where(K8sCluster.name == ref))
    if cluster is None:
        raise ToolError(f"No cluster called {ref!r}.")
    try:
        body = InvestigationIn(
            cluster_id=cluster.id,
            namespace=str(args.get("namespace", "")),
            kind=str(args.get("kind") or "Deployment"),
            name=str(args.get("name", "")),
            hours=int(args.get("hours", 6)),
        )
    except ValueError as exc:
        raise ToolError(f"Invalid request: {exc}") from exc
    _, result, start, end = _run(db, body, None)
    inv = K8sInvestigation(
        org_id=auth.org_id,
        cluster_id=cluster.id,
        namespace=body.namespace,
        kind=body.kind,
        name=body.name,
        window_start=start,
        window_end=end,
        trigger="mcp",
        title=f"{result['target']}: {result['summary']}"[:300],
        result=result,
        created_by_id=auth.user.id,
    )
    db.add(inv)
    db.flush()
    return {
        "id": str(inv.id),
        "title": inv.title,
        "summary": result.get("summary"),
        "causes": result.get("causes", [])[:5],
        "link": f"/kubernetes/investigations/{inv.id}",
    }


def _auth(request: Request, db: SessionDep) -> AuthContext:
    from costlens.api.deps import get_optional_auth

    auth = get_optional_auth(request, db)
    return get_current_auth(request, auth)


@router.post("/mcp", response_model=None, dependencies=[plan_feature("mcp")])
async def mcp(  # noqa: PLR0911 - one reply per JSON-RPC method
    request: Request, db: SessionDep, auth: Annotated[AuthContext, Depends(_auth)]
) -> Response:
    if auth.org_id is None or auth.role is None or not has_permission(auth.role, Permission.READ):
        return JSONResponse(
            _rpc(None, error={"code": -32001, "message": "No organisation access."}),
            status_code=403,
        )
    try:
        msg = json.loads(await request.body())
    except ValueError:
        return JSONResponse(
            _rpc(None, error={"code": -32700, "message": "Parse error"}), status_code=400
        )
    if isinstance(msg, list):
        return JSONResponse(
            _rpc(None, error={"code": -32600, "message": "Batches are not supported"}),
            status_code=400,
        )
    method, id_ = msg.get("method"), msg.get("id")
    if id_ is None:  # notifications (e.g. notifications/initialized)
        return Response(status_code=202)
    params = msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return JSONResponse(
            _rpc(
                id_,
                {
                    "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "costlens", "version": __version__},
                    "instructions": INSTRUCTIONS,
                },
            )
        )
    if method == "ping":
        return JSONResponse(_rpc(id_, {}))
    if method == "tools/list":
        tools = TOOLS + ([INVESTIGATE_TOOL] if auth.api_key_can_investigate else [])
        return JSONResponse(_rpc(id_, {"tools": tools}))
    if method == "tools/call":
        name = params.get("name")
        try:
            data = _call(str(name), params.get("arguments") or {}, db, auth)
        except ToolError as exc:
            return JSONResponse(
                _rpc(id_, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
            )
        structured = data if isinstance(data, dict) else {"items": data}
        return JSONResponse(
            _rpc(
                id_,
                {
                    "content": [{"type": "text", "text": json.dumps(data, default=str)}],
                    "structuredContent": structured,
                    "isError": False,
                },
            )
        )
    return JSONResponse(_rpc(id_, error={"code": -32601, "message": f"Method not found: {method}"}))
