"""
schema/case.py

The Case model — an investigation that groups source documents,
entities, and assigned investigators together. Referenced by
schema.user.get_user_cases (as case IDs) and by
schema.entities.SourceDocument.case_id.

Like schema/user.py, the in-memory _CASE_STORE here is a reference
implementation of this module's pure logic; the live API persists
cases through db/repository.py instead.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class CaseStatus(str, Enum):
    """Lifecycle states a Case can be in."""

    OPEN = "open"
    UNDER_REVIEW = "under_review"
    CLOSED = "closed"


class CaseConfidentiality(str, Enum):
    """How much of a case's detail is exposed to a user who is NOT
    authorized to view it, when a cross-case entity match surfaces it
    to them (see api/routes/cross_case.py). Settable only by
    ADMIN/SUPER_ADMIN (enforced in api/routes/cases.py's confidentiality
    endpoint) — an Investigator cannot mark their own case restricted.

    NORMAL: an unauthorized viewer sees a short summary plus the
    owning agency/department, alongside a "Request Access" action.
    RESTRICTED: an unauthorized viewer sees ONLY the owning agency/
    department name — no title, no summary — alongside "Request
    Access". Intended for highly confidential or national-level-risk
    cases.
    """

    NORMAL = "normal"
    RESTRICTED = "restricted"


@dataclass
class Case:
    """An investigation, scoped to one agency."""

    id: str
    title: str
    agency_id: str
    description: str = ""
    status: CaseStatus = CaseStatus.OPEN
    confidentiality: CaseConfidentiality = CaseConfidentiality.NORMAL
    created_by_user_id: Optional[str] = None
    opened_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    assigned_investigator_ids: list = field(default_factory=list)

    def __post_init__(self):
        """Validate on construction."""
        if not self.id:
            raise ValueError("Case.id must be non-empty")
        if not self.title or not self.title.strip():
            raise ValueError("Case.title must be non-empty")
        if not self.agency_id:
            raise ValueError("Case.agency_id must be non-empty")

    def to_dict(self) -> dict:
        """Serialize to a plain dict (e.g. for an API JSON response)."""
        return {
            "id": self.id,
            "title": self.title,
            "agency_id": self.agency_id,
            "description": self.description,
            "status": self.status.value,
            "confidentiality": self.confidentiality.value,
            "created_by_user_id": self.created_by_user_id,
            "opened_at": self.opened_at,
            "assigned_investigator_ids": list(self.assigned_investigator_ids),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Case":
        """Build and validate a Case from a plain dict."""
        try:
            status = CaseStatus(data.get("status", CaseStatus.OPEN.value))
        except ValueError:
            raise ValueError(f"Unknown case status: {data.get('status')!r}")
        try:
            confidentiality = CaseConfidentiality(data.get("confidentiality", CaseConfidentiality.NORMAL.value))
        except ValueError:
            raise ValueError(f"Unknown case confidentiality: {data.get('confidentiality')!r}")
        try:
            return cls(
                id=data["id"],
                title=data["title"],
                agency_id=data["agency_id"],
                description=data.get("description", ""),
                status=status,
                confidentiality=confidentiality,
                created_by_user_id=data.get("created_by_user_id"),
                opened_at=data.get("opened_at", datetime.now(timezone.utc).isoformat()),
                assigned_investigator_ids=list(data.get("assigned_investigator_ids", [])),
            )
        except KeyError as exc:
            raise ValueError(f"Case dict missing required key: {exc}")


# In-memory reference store — see the module docstring. The live API
# persists through db/repository.py instead.
_CASE_STORE: dict[str, Case] = {}


def register_case(case: Case) -> None:
    """Add a case to the in-memory store."""
    _CASE_STORE[case.id] = case


def get_case(case_id: str) -> Optional[Case]:
    """Look up a case by ID, or None."""
    return _CASE_STORE.get(case_id)


def list_cases_for_agency(agency_id: str) -> list[Case]:
    """Return every case belonging to a given agency."""
    return [c for c in _CASE_STORE.values() if c.agency_id == agency_id]


def assign_investigator(case_id: str, user_id: str) -> Optional[Case]:
    """Add a user to a case's assigned investigators (idempotent —
    adding the same user twice is a no-op). Returns the updated Case,
    or None if the case doesn't exist.
    """
    case = _CASE_STORE.get(case_id)
    if case is None:
        return None
    if user_id not in case.assigned_investigator_ids:
        case.assigned_investigator_ids.append(user_id)
    return case
