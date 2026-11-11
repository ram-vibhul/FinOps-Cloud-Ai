"""Organisation roles and what each may do.

Roles are ordered: viewer < member < admin < owner. Fine-grained custom roles
(checklist 8.8) will map onto permissions defined here.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    VIEWER = "viewer"  # read everything in the organisation
    MEMBER = "member"  # + act on findings, comment, create reports/budgets
    ADMIN = "admin"  # + connections, members (not owners), settings, integrations
    OWNER = "owner"  # + billing, ownership, deleting the organisation

    @property
    def rank(self) -> int:
        return _RANK[self]

    def at_least(self, other: Role) -> bool:
        return self.rank >= other.rank


_RANK = {Role.VIEWER: 0, Role.MEMBER: 1, Role.ADMIN: 2, Role.OWNER: 3}


class Permission(StrEnum):
    READ = "read"
    WRITE = "write"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_CONNECTIONS = "manage_connections"
    MANAGE_SETTINGS = "manage_settings"
    MANAGE_BILLING = "manage_billing"
    MANAGE_OWNERS = "manage_owners"
    DELETE_ORGANISATION = "delete_organisation"


_MIN_ROLE: dict[Permission, Role] = {
    Permission.READ: Role.VIEWER,
    Permission.WRITE: Role.MEMBER,
    Permission.MANAGE_MEMBERS: Role.ADMIN,
    Permission.MANAGE_CONNECTIONS: Role.ADMIN,
    Permission.MANAGE_SETTINGS: Role.ADMIN,
    Permission.MANAGE_BILLING: Role.OWNER,
    Permission.MANAGE_OWNERS: Role.OWNER,
    Permission.DELETE_ORGANISATION: Role.OWNER,
}


def has_permission(role: Role, permission: Permission) -> bool:
    return role.at_least(_MIN_ROLE[permission])


def can_assign(actor: Role, current: Role | None, new: Role) -> bool:
    """Admins manage viewers/members/admins; only owners touch owners."""
    if not has_permission(actor, Permission.MANAGE_MEMBERS):
        return False
    touches_owner = new is Role.OWNER or current is Role.OWNER
    return not touches_owner or actor is Role.OWNER
