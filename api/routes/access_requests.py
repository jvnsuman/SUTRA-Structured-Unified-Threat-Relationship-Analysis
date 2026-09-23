"""
api/routes/access_requests.py

Lifecycle endpoints for schema.access_request.AccessRequest — created
via api/routes/cross_case.py's request-access endpoint. See that
module's docstring for the full escalation state diagram:

    pending_investigator --(explicit deny OR 48h timeout)--> pending_admin
    pending_investigator --(approve)--------------------------> approved
    pending_admin --------(approve)--------------------------> approved
    pending_admin --------(deny)-----------------------------> denied

No background scheduler exists in this project (see
db.repository.escalate_timed_out_access_requests's docstring) — every
GET route here calls it first, so a stale pending_investigator request
is escalated the moment anyone next looks at the relevant list, rather
than on a fixed timer.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import get_current_user, require_role
from db import repository as repo
from db.connection import get_db
from ledger.chain import LedgerEventType
from schema.access_request import AccessRequestStatus
from schema.user import Role, User, authorize

router = APIRouter()


@router.get("/pending/mine")
def list_pending_for_investigator(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Requests currently awaiting THIS user's decision, as an
    assigned investigator on the target case — "awaiting your
    decision" for an Investigator's own inbox.
    """
    repo.escalate_timed_out_access_requests(db)
    pending = repo.get_pending_investigator_access_requests_for_user(db, user.id)
    return {"requests": [r.to_dict() for r in pending]}


@router.get("/pending/admin")
def list_pending_for_admin(user: User = Depends(require_role(Role.ADMIN)), db: Session = Depends(get_db)) -> dict:
    """Requests escalated to PENDING_ADMIN — reviewable by any
    Admin/Super Admin, regardless of agency (per project decision:
    cross-case access is a national-level concern, not agency-scoped).
    """
    repo.escalate_timed_out_access_requests(db)
    pending = repo.get_pending_admin_access_requests(db)
    return {"requests": [r.to_dict() for r in pending]}


@router.get("/case/{case_id}")
def list_for_case(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Every access request ever raised against this case — visible
    to anyone already authorized to view the case itself (its
    assigned investigators, or an Admin/Analyst in its owning agency,
    or a Super Admin), so they can see requests already resolved as
    well as pending ones.
    """
    if case_id not in {c.id for c in repo.get_cases_for_user(db, user)}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not authorized to view this case")
    repo.escalate_timed_out_access_requests(db)
    requests = repo.get_access_requests_for_case(db, case_id)
    return {"requests": [r.to_dict() for r in requests]}


def _require_can_act_on_case(db: Session, user: User, case_id: str) -> None:
    """An investigator may only approve/deny requests for cases they
    are themselves assigned to (this is what "the case's investigator"
    means for the pending_investigator stage) — an Admin/Super Admin
    bypasses this via the separate /admin endpoints below instead.
    """
    case = repo.get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if user.id not in case.assigned_investigator_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an investigator assigned to this case can act on this request",
        )


@router.post("/{request_id}/approve")
def approve_request(request_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Approve a request — callable by the target case's assigned
    investigator (while PENDING_INVESTIGATOR) or by an Admin/Super
    Admin (once escalated to PENDING_ADMIN). Grants the requester
    scoped read access to the target case (see
    db.repository.grant_case_access) and marks the request APPROVED.
    """
    repo.escalate_timed_out_access_requests(db)
    access_request = repo.get_access_request(db, request_id)
    if access_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access request not found")

    if access_request.status == AccessRequestStatus.PENDING_INVESTIGATOR:
        _require_can_act_on_case(db, user, access_request.target_case_id)
    elif access_request.status == AccessRequestStatus.PENDING_ADMIN:
        if not authorize(user, Role.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This request has been escalated and now requires an admin",
            )
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This request has already been resolved")

    resolved = repo.resolve_access_request(db, request_id, approve=True, resolved_by_user_id=user.id)
    repo.grant_case_access(db, access_request.target_case_id, access_request.requester_user_id)
    repo.append_ledger_entry(
        db,
        LedgerEventType.ACCESS_REQUEST_RESOLVED,
        {
            "request_id": request_id,
            "target_case_id": access_request.target_case_id,
            "requester_user_id": access_request.requester_user_id,
            "resolution": "approved",
            "resolved_by_user_id": user.id,
        },
    )
    repo.append_audit(db, "access_request_approved", actor=user, target_type="access_request", target_id=request_id,
                      detail={"case_id": access_request.target_case_id, "requester_user_id": access_request.requester_user_id})
    return resolved.to_dict()


@router.post("/{request_id}/deny")
def deny_request(
    request_id: str, data: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    """Deny a request. At PENDING_INVESTIGATOR, a deny is NOT final —
    it escalates to PENDING_ADMIN immediately (per project decision:
    an investigator's "no" isn't the last word; per module docstring).
    At PENDING_ADMIN, a deny is final (DENIED). `data.note` is
    optional context shown to the requester.
    """
    repo.escalate_timed_out_access_requests(db)
    access_request = repo.get_access_request(db, request_id)
    if access_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access request not found")

    note = data.get("note")

    if access_request.status == AccessRequestStatus.PENDING_INVESTIGATOR:
        _require_can_act_on_case(db, user, access_request.target_case_id)
        resolved = repo.deny_access_request(db, request_id, denied_by_user_id=user.id, note=note)
        repo.append_ledger_entry(
            db,
            LedgerEventType.ACCESS_REQUEST_RESOLVED,
            {
                "request_id": request_id,
                "target_case_id": access_request.target_case_id,
                "requester_user_id": access_request.requester_user_id,
                "resolution": "escalated",
                "denied_by_user_id": user.id,
            },
        )
        repo.append_audit(db, "access_request_escalated", actor=user, target_type="access_request", target_id=request_id,
                          detail={"case_id": access_request.target_case_id, "requester_user_id": access_request.requester_user_id,
                                  "denied_by_user_id": user.id, "note": note})
    elif access_request.status == AccessRequestStatus.PENDING_ADMIN:
        if not authorize(user, Role.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This request has been escalated and now requires an admin",
            )
        resolved = repo.resolve_access_request(db, request_id, approve=False, resolved_by_user_id=user.id)
        repo.append_ledger_entry(
            db,
            LedgerEventType.ACCESS_REQUEST_RESOLVED,
            {
                "request_id": request_id,
                "target_case_id": access_request.target_case_id,
                "requester_user_id": access_request.requester_user_id,
                "resolution": "denied",
                "resolved_by_user_id": user.id,
            },
        )
        repo.append_audit(db, "access_request_denied", actor=user, target_type="access_request", target_id=request_id,
                          detail={"case_id": access_request.target_case_id, "requester_user_id": access_request.requester_user_id,
                                  "resolved_by_user_id": user.id, "note": note})
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This request has already been resolved")

    return resolved.to_dict()
