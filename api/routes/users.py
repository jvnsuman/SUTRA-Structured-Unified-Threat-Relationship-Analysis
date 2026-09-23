"""
api/routes/users.py

Minimal admin-facing user directory and account creation. This
project has no general user-listing endpoint (see db/repository.py:
only get_user_by_badge_id and get_user_by_id exist) — the routes
below cover exactly what the dashboard's Admin page needs, not a full
user-management surface:

  - GET /lookup/{badge_id} resolves a badge ID to a user_id, for the
    "assign investigator" flow (POST /cases/{case_id}/assign takes a
    user_id, not a badge_id).
  - GET /{user_id} resolves a user_id back to a display name/badge_id,
    for showing a case's assigned_investigator_ids (which are user_ids)
    as readable names in the same Admin page.
  - POST / creates a new account. An ADMIN may only create
    investigator/analyst accounts within their own agency — not
    another admin or super_admin, so a compromised/malicious admin
    account can't mint itself a peer. A SUPER_ADMIN may create any
    role, in any agency.

There's still no account deactivation here — see
api/auth.py's _resolve_token docstring, which already anticipates
re-checking a "deactivation" that doesn't exist yet; that needs a new
column + migration and is deliberately left for a separate change.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import require_role
from config import get_settings
from db import repository as repo
from db.connection import get_db
from schema.user import Role, User, hash_password

router = APIRouter()

_ADMIN_CREATABLE_ROLES = {Role.INVESTIGATOR, Role.ANALYST}


def _serialize(found: User) -> dict:
    return {
        "id": found.id,
        "name": found.name,
        "badge_id": found.badge_id,
        "agency_id": found.agency_id,
        "role": found.role.value,
    }


def _check_agency_scope(found: User, user: User) -> None:
    if found.agency_id != user.agency_id and user.role != Role.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only look up users within your own agency",
        )


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_user_endpoint(
    data: dict, user: User = Depends(require_role(Role.ADMIN)), db: Session = Depends(get_db)
) -> dict:
    """Create a new account. Requires {"name", "badge_id", "password",
    "role"}; "agency_id" is optional and defaults to the caller's own
    agency. An ADMIN may only create investigator/analyst accounts in
    their own agency; a SUPER_ADMIN may create any role in any agency
    (must already exist — see db.repository.get_agency).
    """
    name = data.get("name")
    badge_id = data.get("badge_id")
    password = data.get("password")
    raw_role = data.get("role")

    if not name or not str(name).strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    if not badge_id or not str(badge_id).strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="badge_id is required")
    if not password or len(password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="password must be at least 8 characters")

    try:
        role = Role(raw_role)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"role must be one of: {[r.value for r in Role]}"
        )

    if user.role != Role.SUPER_ADMIN and role not in _ADMIN_CREATABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"An admin may only create accounts with role: {[r.value for r in _ADMIN_CREATABLE_ROLES]}",
        )

    agency_id = data.get("agency_id", user.agency_id)
    if agency_id != user.agency_id and user.role != Role.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only create accounts within your own agency",
        )
    if user.role == Role.SUPER_ADMIN and repo.get_agency(db, agency_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No agency with that agency_id")

    if repo.get_user_by_badge_id(db, badge_id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That badge ID is already in use")

    new_user = User(
        id=str(uuid.uuid4()),
        name=str(name).strip(),
        badge_id=str(badge_id).strip(),
        agency_id=agency_id,
        role=role,
        password_hash=hash_password(password, iterations=get_settings().pbkdf2_iterations),
    )
    created = repo.create_user(db, new_user)
    repo.append_audit(db, "user_created", actor=user, target_type="user", target_id=created.id,
                      detail={"badge_id": created.badge_id, "role": created.role.value, "agency_id": created.agency_id})
    return _serialize(created)


@router.get("/lookup/{badge_id}")
def lookup_user_by_badge_id(
    badge_id: str, user: User = Depends(require_role(Role.ADMIN)), db: Session = Depends(get_db)
) -> dict:
    """Resolve a badge ID to a user's id/name/agency/role, for the
    Admin page's "assign investigator" flow. An ADMIN may only look up
    users in their own agency; SUPER_ADMIN can look up anyone (same
    scoping rule as cases.py's assign/confidentiality endpoints).
    """
    found = repo.get_user_by_badge_id(db, badge_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No user with that badge ID")
    _check_agency_scope(found, user)
    return _serialize(found)


@router.get("/{user_id}")
def get_user_by_id_endpoint(
    user_id: str, user: User = Depends(require_role(Role.ADMIN)), db: Session = Depends(get_db)
) -> dict:
    """Resolve an internal user_id to a user's id/name/agency/role —
    the reverse of the lookup above, for displaying names next to the
    raw ids in a case's assigned_investigator_ids. Same agency scoping
    as lookup_user_by_badge_id.
    """
    found = repo.get_user_by_id(db, user_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No user with that id")
    _check_agency_scope(found, user)
    return _serialize(found)
