from __future__ import annotations
from cityboymandate.core.models import Principal, Role

PERMISSIONS: dict[Role, set[str]] = {
    Role.VIEWER: {"read"},
    Role.ANALYST: {"read", "investigate", "draft", "rules.read"},
    Role.REVIEWER: {"read", "investigate", "draft", "rules.read", "review", "publish", "emergency_stop"},
    Role.ADMIN: {"read", "investigate", "draft", "rules.read", "rules.write", "sources.write", "review", "publish",
                 "settings.write", "users.write", "emergency_stop", "emergency_clear"},
    # The pipeline itself: no review, no settings, no publishing, no rule edits.
    Role.AGENT: {"read", "investigate", "draft"},
}
REVIEW_ROLES = {Role.REVIEWER, Role.ADMIN}


class PermissionDenied(Exception):
    pass


def require(p: Principal, perm: str) -> None:
    if perm not in PERMISSIONS.get(p.role, set()):
        raise PermissionDenied(f"{p.role.value} lacks permission '{perm}'")
