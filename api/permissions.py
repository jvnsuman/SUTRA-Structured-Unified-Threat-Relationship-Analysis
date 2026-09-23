"""
api/permissions.py

Case-level WRITE authorization, shared by every route that changes a
case's data (ingestion, resolution overrides). Reading is governed by
db.repository.get_cases_for_user; writing is stricter:

  * INVESTIGATOR: only cases they are explicitly assigned to.
  * SUPER_ADMIN: any case (break-glass; the action is audit-logged).
  * ANALYST is read-only by definition; ADMIN manages accounts, not
    case content. Both get 403.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from db import repository as repo
from schema.user import Role, User


def require_case_write(db: Session, user: User, case_id: str) -> None:
    case = repo.get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if user.role == Role.SUPER_ADMIN:
        return
    if user.role == Role.INVESTIGATOR and case_id in {c.id for c in repo.get_cases_for_user(db, user)}:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only an investigator assigned to this case can add or change its data",
    )


def require_case_read(db: Session, user: User, case_id: str) -> None:
    if case_id not in {c.id for c in repo.get_cases_for_user(db, user)}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not authorized to view this case")
