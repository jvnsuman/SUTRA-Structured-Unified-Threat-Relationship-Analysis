"""
schema/access_request.py

An AccessRequest is created when an Investigator, viewing a cross-case
entity match (nlp.resolution finding the same person/phone/vehicle in
a case they are not authorized to view — see api/routes/cross_case.py),
asks to view that other case.

Escalation lifecycle (see api/routes/access_requests.py for the actual
state transitions):

    pending_investigator --(explicit deny OR 48h timeout)--> pending_admin
    pending_investigator --(approve)--------------------------> approved
    pending_admin --------(approve)--------------------------> approved
    pending_admin --------(deny)-----------------------------> denied

A request always starts pending_investigator, routed to the target
case's assigned investigator(s). Only ADMIN/SUPER_ADMIN (any agency —
per project decision, cross-case access is a national-level concern,
not agency-scoped) can act once it reaches pending_admin.

Kept as its own module (rather than folded into schema/case.py) since
it is a distinct entity with its own lifecycle, not a property of a
Case or a User.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

# How long a request waits for the target case's investigator(s) to
# act before auto-escalating to Admin/Super Admin (project decision:
# 48 hours; deliberately a plain constant, not configurable per-case,
# since there's currently no product requirement for that).
ESCALATION_TIMEOUT = timedelta(hours=48)


class AccessRequestStatus(str, Enum):
    """Lifecycle states of an AccessRequest — see module docstring for
    the transition diagram.
    """

    PENDING_INVESTIGATOR = "pending_investigator"
    PENDING_ADMIN = "pending_admin"
    APPROVED = "approved"
    DENIED = "denied"


@dataclass
class AccessRequest:
    """A request by one user to view a case they aren't authorized
    for, raised from a cross-case entity match.
    """

    id: str
    requester_user_id: str
    target_case_id: str
    matched_entity_id: str  # the ResolvedEntity.id that triggered this request — see nlp.resolution
    reason: str  # free-text context the requester gives, shown to the approver
    status: AccessRequestStatus = AccessRequestStatus.PENDING_INVESTIGATOR
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    escalated_at: Optional[str] = None
    resolved_at: Optional[str] = None
    resolved_by_user_id: Optional[str] = None
    denial_note: Optional[str] = None  # set when an investigator denies, shown to the requester before admin escalation

    def __post_init__(self):
        """Validate on construction."""
        if not self.id:
            raise ValueError("AccessRequest.id must be non-empty")
        if not self.requester_user_id:
            raise ValueError("AccessRequest.requester_user_id must be non-empty")
        if not self.target_case_id:
            raise ValueError("AccessRequest.target_case_id must be non-empty")
        if not self.matched_entity_id:
            raise ValueError("AccessRequest.matched_entity_id must be non-empty")
        if not self.reason or not self.reason.strip():
            raise ValueError("AccessRequest.reason must be non-empty")

    def is_timed_out(self, now: Optional[datetime] = None) -> bool:
        """True if this request has sat in PENDING_INVESTIGATOR longer
        than ESCALATION_TIMEOUT and should be escalated. `now` is
        injectable for testing; defaults to the real current time.
        """
        if self.status != AccessRequestStatus.PENDING_INVESTIGATOR:
            return False
        current_time = now or datetime.now(timezone.utc)
        created = datetime.fromisoformat(self.created_at)
        return current_time - created >= ESCALATION_TIMEOUT

    def to_dict(self) -> dict:
        """Serialize to a plain dict (e.g. for an API JSON response)."""
        return {
            "id": self.id,
            "requester_user_id": self.requester_user_id,
            "target_case_id": self.target_case_id,
            "matched_entity_id": self.matched_entity_id,
            "reason": self.reason,
            "status": self.status.value,
            "created_at": self.created_at,
            "escalated_at": self.escalated_at,
            "resolved_at": self.resolved_at,
            "resolved_by_user_id": self.resolved_by_user_id,
            "denial_note": self.denial_note,
        }


def new_access_request(requester_user_id: str, target_case_id: str, matched_entity_id: str, reason: str) -> AccessRequest:
    """Construct a fresh AccessRequest with a generated ID — the
    normal way routes should build one, so ID generation lives in one
    place rather than being repeated at every call site.
    """
    return AccessRequest(
        id=str(uuid.uuid4()),
        requester_user_id=requester_user_id,
        target_case_id=target_case_id,
        matched_entity_id=matched_entity_id,
        reason=reason,
    )
