"""
api/routes/cases.py

Case creation, listing, investigator assignment/removal, and
lifecycle status. Every route here enforces agency scoping: an ADMIN can only manage
cases and assign investigators within their own agency; only a
SUPER_ADMIN can act across agencies. The status endpoint is the one
exception to "admin-only" — an assigned investigator may also update
it, since it's a working-status field, not an access-control setting
like confidentiality. See schema.user.Role for the full role
hierarchy.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import get_current_user, require_role
from db import repository as repo
from db.connection import get_db
from ledger.chain import LedgerEventType
from schema.case import Case, CaseConfidentiality, CaseStatus
from schema.user import Role, User

router = APIRouter()


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_case(data: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Create a new case, defaulting to the creator's own agency. Only
    a SUPER_ADMIN may create a case for a different agency. The
    creator is automatically assigned as an investigator on the case.

    Role-gated the same way api/permissions.py's require_case_write
    gates writes on an existing case: INVESTIGATOR or SUPER_ADMIN
    only. ANALYST is read-only by definition (SECURITY.md) and ADMIN
    manages accounts, not case content -- neither may originate a
    case. This can't reuse require_role() here, since ADMIN/
    SUPER_ADMIN implying ANALYST (schema.user._ROLE_IMPLIES) would
    wrongly let ADMIN through a require_role(Role.ANALYST) gate.
    """
    if user.role not in (Role.INVESTIGATOR, Role.SUPER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an investigator or super admin can create a case",
        )

    title = data.get("title")
    if not title or not str(title).strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="title is required")

    description = data.get("description")
    if not description or not str(description).strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="description is required")

    agency_id = data.get("agency_id", user.agency_id)
    if agency_id != user.agency_id and user.role != Role.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a super admin can create a case for another agency",
        )

    case = Case(
        id=str(uuid.uuid4()),
        title=title,
        agency_id=agency_id,
        description=str(description).strip(),
        created_by_user_id=user.id,
    )
    created = repo.create_case(db, case)
    created = repo.assign_investigator(db, created.id, user.id)
    repo.append_ledger_entry(
        db,
        LedgerEventType.CASE_CREATED,
        {"case_id": created.id, "title": created.title, "agency_id": created.agency_id, "created_by_user_id": user.id},
    )

    repo.append_audit(db, "case_created", actor=user, target_type="case", target_id=created.id,
                      detail={"title": created.title})
    return created.to_dict()


@router.get("/")
def list_cases(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """List every case this user is authorized to see (role/agency
    scoped — see db.repository.get_cases_for_user).
    """
    cases = repo.get_cases_for_user(db, user)
    return {"cases": [c.to_dict() for c in cases]}


@router.get("/{case_id}")
def get_case_endpoint(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Fetch a single case by ID, if the user is authorized to see it."""
    if case_id not in {c.id for c in repo.get_cases_for_user(db, user)}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not authorized to view this case")
    case = repo.get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case.to_dict()


@router.post("/{case_id}/assign")
def assign_investigator_endpoint(
    case_id: str, data: dict, user: User = Depends(require_role(Role.ADMIN)), db: Session = Depends(get_db)
) -> dict:
    """Assign an investigator (by user_id) to a case. Requires ADMIN
    or SUPER_ADMIN. An ADMIN may only manage cases in their own
    agency; SUPER_ADMIN can act across agencies.
    """
    case = repo.get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.agency_id != user.agency_id and user.role != Role.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only manage cases within your own agency",
        )

    investigator_id = data.get("user_id")
    if not investigator_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id is required")

    updated = repo.assign_investigator(db, case_id, investigator_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case or user not found")
    repo.append_audit(db, "investigator_assigned", actor=user, target_type="case", target_id=case_id,
                      detail={"investigator_id": investigator_id})

    return updated.to_dict()


@router.post("/{case_id}/unassign")
def unassign_investigator_endpoint(
    case_id: str, data: dict, user: User = Depends(require_role(Role.ADMIN)), db: Session = Depends(get_db)
) -> dict:
    """Remove an investigator (by user_id) from a case. Requires ADMIN
    or SUPER_ADMIN, agency-scoped the same way as the assign endpoint
    above. Refuses to remove a case's last remaining investigator —
    that would leave the case invisible to every Investigator-role
    user (see db.repository.get_cases_for_user), reachable only by
    re-assigning through an Admin. Use assign to add a replacement
    first if that's the goal.
    """
    case = repo.get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.agency_id != user.agency_id and user.role != Role.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only manage cases within your own agency",
        )

    investigator_id = data.get("user_id")
    if not investigator_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id is required")

    if case.assigned_investigator_ids == [investigator_id]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove a case's last remaining investigator — assign a replacement first",
        )

    updated = repo.unassign_investigator(db, case_id, investigator_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    repo.append_audit(db, "investigator_unassigned", actor=user, target_type="case", target_id=case_id,
                      detail={"investigator_id": investigator_id})

    return updated.to_dict()


@router.post("/{case_id}/confidentiality")
def set_case_confidentiality_endpoint(
    case_id: str, data: dict, user: User = Depends(require_role(Role.ADMIN)), db: Session = Depends(get_db)
) -> dict:
    """Set a case's confidentiality tier (schema.case.CaseConfidentiality:
    "normal" or "restricted"). Requires ADMIN or SUPER_ADMIN — an
    Investigator cannot mark their own case restricted. An ADMIN may
    only change cases in their own agency; SUPER_ADMIN can act across
    agencies. Restricted cases show only their owning department to
    an unauthorized cross-case viewer, with no title or summary — see
    api/routes/cross_case.py.
    """
    case = repo.get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.agency_id != user.agency_id and user.role != Role.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only manage cases within your own agency",
        )

    raw_confidentiality = data.get("confidentiality")
    try:
        confidentiality = CaseConfidentiality(raw_confidentiality)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"confidentiality must be one of: {[c.value for c in CaseConfidentiality]}",
        )

    updated = repo.set_case_confidentiality(db, case_id, confidentiality)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    repo.append_audit(db, "case_confidentiality_changed", actor=user, target_type="case", target_id=case_id,
                      detail={"confidentiality": confidentiality.value})

    return updated.to_dict()


@router.post("/{case_id}/status")
def set_case_status_endpoint(
    case_id: str, data: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    """Set a case's lifecycle status (schema.case.CaseStatus: "open",
    "under_review", or "closed"). Callable by an investigator assigned
    to the case, or by an ADMIN/SUPER_ADMIN (an ADMIN limited to their
    own agency, same scoping rule as assign/confidentiality above) —
    unlike confidentiality, this is a working-status field the
    investigator running the case should be able to update day to
    day, not an admin-only control.
    """
    case = repo.get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    is_assigned_investigator = user.id in case.assigned_investigator_ids
    is_scoped_admin = user.role in (Role.ADMIN, Role.SUPER_ADMIN) and (
        case.agency_id == user.agency_id or user.role == Role.SUPER_ADMIN
    )
    if not (is_assigned_investigator or is_scoped_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an investigator assigned to this case, or an admin, can change its status",
        )

    raw_status = data.get("status")
    try:
        case_status = CaseStatus(raw_status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"status must be one of: {[s.value for s in CaseStatus]}",
        )

    updated = repo.set_case_status(db, case_id, case_status)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    repo.append_audit(db, "case_status_changed", actor=user, target_type="case", target_id=case_id,
                      detail={"status": case_status.value})

    return updated.to_dict()
