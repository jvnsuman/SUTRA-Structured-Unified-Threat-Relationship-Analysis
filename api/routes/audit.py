"""
api/routes/audit.py

Read access to the audit log (db.models.AuditLogORM): logins, failed
logins, password changes, user creation, case assignment/status/
confidentiality changes, access-request decisions, resolution overrides,
report generation. Restricted to ADMIN / SUPER_ADMIN, as the role
description promises ("manage accounts + audit logs").
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.auth import require_role
from db import repository as repo
from db.connection import get_db
from schema.user import Role, User

router = APIRouter()


@router.get("/")
def list_audit_entries(
    limit: int = 100,
    offset: int = 0,
    action: Optional[str] = None,
    user: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    """Newest first. `limit` is capped at 500. Optional `action` filter
    (e.g. login_failed)."""
    return {"entries": repo.list_audit(db, limit=max(1, limit), offset=max(0, offset), action=action)}
