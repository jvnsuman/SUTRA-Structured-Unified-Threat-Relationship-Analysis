"""
db/repository.py

The persistence adapter: converts between db.models' ORM rows and the
plain domain dataclasses (schema.user.Agency/User, schema.case.Case).
Callers (api/auth.py, api/routes/) work entirely in terms of the
dataclasses and never see an ORM object — this is the one place that
translation happens, so the rest of the app doesn't need to know
SQLAlchemy exists.
"""

import json
import random
import uuid
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    AccessRequestORM,
    AgencyORM,
    AuditLogORM,
    CaseORM,
    EntityORM,
    EvidenceLinkORM,
    LedgerEntryORM,
    RelationshipORM,
    ReportORM,
    ResolutionOverrideORM,
    SourceDocumentORM,
    UserORM,
)
from ledger.chain import LedgerEntry, LedgerEventType, build_genesis_entry, build_next_entry
from nlp.extraction import EntityType as NlpEntityType
from nlp.extraction import ExtractedEntity
from nlp.relation_classification import ClassifiedRelation
from nlp.relation_classification import RelationType as NlpRelationType
from schema.access_request import AccessRequest, AccessRequestStatus
from schema.case import Case, CaseConfidentiality, CaseStatus
from schema.entities import SourceDocument
from schema.report import Report
from schema.user import Agency, Role, User


def _agency_to_domain(row: AgencyORM) -> Agency:
    """Convert an AgencyORM row into a schema.user.Agency dataclass."""
    return Agency(
        id=row.id,
        name=row.name,
        agency_type=row.agency_type,
        parent_agency_id=row.parent_agency_id,
        is_active=row.is_active == "true",
    )


def _user_to_domain(row: UserORM) -> User:
    """Convert a UserORM row into a schema.user.User dataclass."""
    return User(
        id=row.id,
        name=row.name,
        badge_id=row.badge_id,
        agency_id=row.agency_id,
        role=Role(row.role),
        password_hash=row.password_hash,
        last_login=row.last_login,
        preferences=json.loads(row.preferences) if row.preferences else {},
    )


def _case_to_domain(row: CaseORM) -> Case:
    """Convert a CaseORM row (with its investigators relationship
    already loaded) into a schema.case.Case dataclass.
    """
    return Case(
        id=row.id,
        title=row.title,
        agency_id=row.agency_id,
        description=row.description or "",
        status=CaseStatus(row.status),
        confidentiality=CaseConfidentiality(row.confidentiality),
        created_by_user_id=row.created_by_user_id,
        opened_at=row.opened_at,
        assigned_investigator_ids=[u.id for u in row.investigators],
    )


def _document_to_domain(row: SourceDocumentORM) -> SourceDocument:
    """Convert a SourceDocumentORM row into a schema.entities.SourceDocument.

    structured follows the same json.dumps/json.loads-as-string
    pattern already used for UserORM.preferences above — decoded here,
    defaulting to {} when the column is null (FIR/surveillance/etc
    documents with no structured equivalent).
    """
    return SourceDocument(
        id=row.id,
        document_type=row.document_type,
        raw_text=row.raw_text,
        case_id=row.case_id,
        created_at=row.created_at,
        structured=json.loads(row.structured) if row.structured else {},
    )


def _report_to_domain(row: ReportORM) -> Report:
    """Convert a ReportORM row into a schema.report.Report."""
    return Report(
        id=row.id,
        case_id=row.case_id,
        title=row.title,
        format=row.format,
        content=row.content,
        created_at=row.created_at,
    )


def create_agency(db: Session, agency: Agency) -> Agency:
    """Insert a new agency and return it as a domain dataclass."""
    row = AgencyORM(
        id=agency.id,
        name=agency.name,
        agency_type=agency.agency_type,
        parent_agency_id=agency.parent_agency_id,
        is_active="true" if agency.is_active else "false",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _agency_to_domain(row)


def get_agency(db: Session, agency_id: str) -> Optional[Agency]:
    """Look up an agency by ID, or None."""
    row = db.get(AgencyORM, agency_id)
    return _agency_to_domain(row) if row else None


def create_user(db: Session, user: User) -> User:
    """Insert a new user and return it as a domain dataclass. Expects
    user.password_hash to already be set (see schema.user.hash_password) —
    this function does not hash it.
    """
    row = UserORM(
        id=user.id,
        name=user.name,
        badge_id=user.badge_id,
        agency_id=user.agency_id,
        role=user.role.value,
        password_hash=user.password_hash,
        last_login=user.last_login,
        preferences=json.dumps(user.preferences) if user.preferences else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _user_to_domain(row)


def get_user_by_badge_id(db: Session, badge_id: str) -> Optional[User]:
    """Look up a user by badge_id, or None. Used by api/auth.py on
    every login and every authenticated request.
    """
    row = db.query(UserORM).filter(UserORM.badge_id == badge_id).one_or_none()
    return _user_to_domain(row) if row else None


def get_user_by_id(db: Session, user_id: str) -> Optional[User]:
    """Look up a user by their internal id, or None. Used by
    api/routes/users.py to resolve a case's assigned_investigator_ids
    (internal ids) back into display-friendly name/badge_id — there's
    no bulk/listing equivalent (see that module's docstring).
    """
    row = db.get(UserORM, user_id)
    return _user_to_domain(row) if row else None


def update_last_login(db: Session, badge_id: str, timestamp: str) -> None:
    """Stamp a user's last_login time after a successful login. No-op
    if the badge_id doesn't exist (shouldn't happen in practice, since
    the caller just authenticated against this same badge_id).
    """
    row = db.query(UserORM).filter(UserORM.badge_id == badge_id).one_or_none()
    if row is not None:
        row.last_login = timestamp
        db.commit()


def update_password_hash(db: Session, user_id: str, new_password_hash: str) -> bool:
    """Overwrite a user's stored password hash (already hashed by the
    caller — see schema.user.hash_password; this function does not
    hash it itself, same convention as create_user). Returns True if
    the user existed and was updated, False otherwise.
    """
    row = db.get(UserORM, user_id)
    if row is None:
        return False
    row.password_hash = new_password_hash
    db.commit()
    return True


def create_case(db: Session, case: Case) -> Case:
    """Insert a new case and return it as a domain dataclass. Does not
    assign any investigators — call assign_investigator separately.
    """
    row = CaseORM(
        id=case.id,
        title=case.title,
        agency_id=case.agency_id,
        description=case.description,
        status=case.status.value,
        confidentiality=case.confidentiality.value,
        created_by_user_id=case.created_by_user_id,
        opened_at=case.opened_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _case_to_domain(row)


def set_case_confidentiality(db: Session, case_id: str, confidentiality: CaseConfidentiality) -> Optional[Case]:
    """Change a case's confidentiality tier (see schema.case.CaseConfidentiality).
    Route-level enforcement (ADMIN/SUPER_ADMIN only) happens in
    api/routes/cases.py — this function does not itself check the
    caller's role. Returns the updated Case, or None if case_id
    doesn't exist.
    """
    row = db.get(CaseORM, case_id)
    if row is None:
        return None
    row.confidentiality = confidentiality.value
    db.commit()
    db.refresh(row)
    return _case_to_domain(row)


def set_case_status(db: Session, case_id: str, case_status: CaseStatus) -> Optional[Case]:
    """Change a case's lifecycle status (schema.case.CaseStatus: open,
    under_review, closed). Route-level enforcement (assigned
    investigator or ADMIN/SUPER_ADMIN) happens in api/routes/cases.py
    — this function does not itself check the caller's role. Returns
    the updated Case, or None if case_id doesn't exist.
    """
    row = db.get(CaseORM, case_id)
    if row is None:
        return None
    row.status = case_status.value
    db.commit()
    db.refresh(row)
    return _case_to_domain(row)


def get_case(db: Session, case_id: str) -> Optional[Case]:
    """Look up a case by ID, or None."""
    row = db.get(CaseORM, case_id)
    return _case_to_domain(row) if row else None


def get_cases_for_user(db: Session, user: User) -> list[Case]:
    """Return every case this user is authorized to see, scoped by
    role — the DB-backed equivalent of schema.user.get_user_cases:

    - SUPER_ADMIN: every case, across every agency.
    - ANALYST / ADMIN: every case belonging to their own agency.
    - INVESTIGATOR: only cases they're explicitly assigned to.
    """
    if user.role in (Role.SUPER_ADMIN,):
        rows = db.query(CaseORM).all()
    elif user.role in (Role.ANALYST, Role.ADMIN):
        rows = db.query(CaseORM).filter(CaseORM.agency_id == user.agency_id).all()
    else:
        user_row = db.get(UserORM, user.id)
        rows = user_row.assigned_cases if user_row else []
    return [_case_to_domain(row) for row in rows]


def get_all_cases_except(db: Session, exclude_case_ids: set[str]) -> list[Case]:
    """Every case NOT in exclude_case_ids — used by
    api/routes/cross_case.py to scan for matches only in cases the
    current viewer does not already have access to (a case they can
    already see in full is not a "cross-case" match from their point
    of view). Not role-scoped itself — callers are responsible for
    passing an exclude_case_ids set that already reflects the
    viewer's own authorization (e.g. via get_cases_for_user).
    """
    if not exclude_case_ids:
        rows = db.query(CaseORM).all()
    else:
        rows = db.query(CaseORM).filter(CaseORM.id.notin_(exclude_case_ids)).all()
    return [_case_to_domain(row) for row in rows]


def assign_investigator(db: Session, case_id: str, user_id: str) -> Optional[Case]:
    """Add a user to a case's assigned investigators (idempotent).
    Returns the updated Case, or None if either the case or the user
    doesn't exist.
    """
    case_row = db.get(CaseORM, case_id)
    user_row = db.get(UserORM, user_id)
    if case_row is None or user_row is None:
        return None
    if user_row not in case_row.investigators:
        case_row.investigators.append(user_row)
    db.commit()
    db.refresh(case_row)
    return _case_to_domain(case_row)


def unassign_investigator(db: Session, case_id: str, user_id: str) -> Optional[Case]:
    """Remove a user from a case's assigned investigators (idempotent
    — removing someone who isn't currently assigned is a no-op, not
    an error). Returns the updated Case, or None if the case doesn't
    exist. Unlike assign_investigator, a nonexistent user_id is not
    itself an error here: there's nothing to remove, so it's treated
    the same as "already not assigned."
    """
    case_row = db.get(CaseORM, case_id)
    if case_row is None:
        return None
    case_row.investigators = [inv for inv in case_row.investigators if inv.id != user_id]
    db.commit()
    db.refresh(case_row)
    return _case_to_domain(case_row)


def create_document(db: Session, document: SourceDocument) -> SourceDocument:
    """Insert a newly-ingested source document and return it as a
    domain dataclass.

    structured is encoded via json.dumps — same pattern as
    create_user's preferences handling above — and stored as None
    (not an empty-object string) when the document has no structured
    data, keeping the column consistent with preferences's null-vs-
    empty convention.
    """
    row = SourceDocumentORM(
        id=document.id,
        document_type=document.document_type,
        raw_text=document.raw_text,
        case_id=document.case_id,
        structured=json.dumps(document.structured) if document.structured else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _document_to_domain(row)


def get_document(db: Session, document_id: str) -> Optional[SourceDocument]:
    """Look up a source document by ID, or None. Used by
    api/routes/ingestion.py to reject duplicate ingestion of the same
    document ID.
    """
    row = db.get(SourceDocumentORM, document_id)
    return _document_to_domain(row) if row else None


def get_documents_for_case(db: Session, case_id: str) -> list[SourceDocument]:
    """Every source document ingested under a case, as domain
    dataclasses (with structured decoded) — the input
    api/routes/alerts.py hands to graph.analytics.detect_anomalies
    alongside that case's graph, so the financial-structuring and
    communication-burst checks can run against real ingested CDR/
    financial documents, not just synthetic ones (see that module's
    docstring for the gap this closes).
    """
    rows = db.query(SourceDocumentORM).filter(SourceDocumentORM.case_id == case_id).all()
    return [_document_to_domain(row) for row in rows]


def get_document_summary_for_case(db: Session, case_id: str) -> list[dict]:
    """Per-document-type counts and the most recent created_at
    timestamp for every source document ingested under a case — the
    real data behind the Data Sources page's summary table (and the
    Reports page's generated summaries). Grouped in Python rather
    than SQL to stay agnostic of SQLite/PostgreSQL dialect differences.
    """
    rows = db.query(SourceDocumentORM).filter(SourceDocumentORM.case_id == case_id).all()
    summary: dict[str, dict] = {}
    for row in rows:
        entry = summary.setdefault(
            row.document_type, {"document_type": row.document_type, "count": 0, "last_updated": None}
        )
        entry["count"] += 1
        if entry["last_updated"] is None or row.created_at > entry["last_updated"]:
            entry["last_updated"] = row.created_at
    return sorted(summary.values(), key=lambda e: e["document_type"])


def update_user_preferences(db: Session, user_id: str, updates: dict) -> Optional[User]:
    """Merge `updates` into a user's stored preferences (Settings page
    toggles) and return the updated User, or None if user_id doesn't
    exist. A merge rather than a replace, so toggling one preference
    from the frontend never clobbers the others.
    """
    row = db.get(UserORM, user_id)
    if row is None:
        return None
    current = json.loads(row.preferences) if row.preferences else {}
    current.update(updates)
    row.preferences = json.dumps(current)
    db.commit()
    db.refresh(row)
    return _user_to_domain(row)


def _entity_to_domain(row: EntityORM) -> ExtractedEntity:
    """Convert an EntityORM row back into an
    nlp.extraction.ExtractedEntity, the shape nlp.resolution.resolve_entities
    and graph.build.build_graph expect as input.

    ExtractedEntity.metadata (engine, alias_group, nearby_ids, script,
    start_char/end_char) is stored as JSON in EntityORM.metadata_json and
    restored here: nlp.resolution's multi-signal scoring depends on
    alias_group and nearby_ids surviving the round trip.
    """
    metadata = {}
    if row.metadata_json:
        try:
            metadata = json.loads(row.metadata_json)
        except (TypeError, ValueError):
            metadata = {}
    return ExtractedEntity(
        id=row.id,
        text=row.raw_text,
        entity_type=NlpEntityType(row.entity_type),
        source_doc_id=row.source_document_id,
        start_char=int(metadata.get("start_char", 0) or 0),
        end_char=int(metadata.get("end_char", 0) or 0),
        confidence=row.confidence or 0.0,
        normalized_text=row.normalized_text,
        metadata=metadata,
    )


def _relationship_to_domain(row: RelationshipORM) -> ClassifiedRelation:
    """Convert a RelationshipORM row back into a
    nlp.relation_classification.ClassifiedRelation, the shape
    graph.build.build_graph expects for its `relations` argument.
    """
    return ClassifiedRelation(
        id=row.id,
        entity_a_id=row.source_entity_id,
        entity_b_id=row.target_entity_id,
        relation_type=NlpRelationType(row.relationship_type),
        confidence=row.weight,
        source_doc_id=row.source_document_id,
    )


def create_entities(db: Session, entities: list[ExtractedEntity]) -> None:
    """Persist a batch of extracted entities (raw per-mention rows,
    not yet resolved/deduplicated — resolution happens at query time
    via nlp.resolution.resolve_entities, so raw mentions are what's
    stored here). No-op on an empty list.

    Called from api/routes/ingestion.py right after
    nlp.extraction.extract_entities succeeds for a document.
    """
    if not entities:
        return
    for entity in entities:
        meta = dict(entity.metadata or {})
        meta["start_char"], meta["end_char"] = entity.start_char, entity.end_char
        db.add(EntityORM(
            id=entity.id,
            entity_type=entity.entity_type.value,
            raw_text=entity.text,
            normalized_text=entity.normalized_text,
            confidence=entity.confidence,
            source_document_id=entity.source_doc_id,
            metadata_json=json.dumps(meta, default=str),
        ))
    db.commit()


def get_entities_for_case(db: Session, case_id: str) -> list[ExtractedEntity]:
    """Every extracted entity mention across every document ingested
    under a case — the input api/routes/query.py hands to
    nlp.resolution.resolve_entities to build that case's graph.
    """
    rows = (
        db.query(EntityORM)
        .join(SourceDocumentORM, EntityORM.source_document_id == SourceDocumentORM.id)
        .filter(SourceDocumentORM.case_id == case_id)
        .all()
    )
    return [_entity_to_domain(row) for row in rows]


def create_relationships(db: Session, relations: list[ClassifiedRelation]) -> None:
    """Persist a batch of classified relations. No-op on an empty
    list (e.g. when nlp.relation_classification's heavy dependencies
    aren't installed — see api/routes/ingestion.py's degrade-gracefully
    handling, same pattern as extraction's spaCy-missing case).
    """
    if not relations:
        return
    for relation in relations:
        db.add(RelationshipORM(
            id=relation.id,
            source_entity_id=relation.entity_a_id,
            target_entity_id=relation.entity_b_id,
            relationship_type=relation.relation_type.value,
            source_document_id=relation.source_doc_id,
            weight=relation.confidence,
        ))
    db.commit()


def get_relationships_for_case(db: Session, case_id: str) -> list[ClassifiedRelation]:
    """Every classified relation from every document ingested under a
    case — the input api/routes/query.py hands to graph.build.build_graph
    alongside that case's resolved entities.
    """
    rows = (
        db.query(RelationshipORM)
        .join(SourceDocumentORM, RelationshipORM.source_document_id == SourceDocumentORM.id)
        .filter(SourceDocumentORM.case_id == case_id)
        .all()
    )
    return [_relationship_to_domain(row) for row in rows]


def create_report(db: Session, report: Report) -> Report:
    """Insert a newly-generated report and return it as a domain
    dataclass (with created_at populated from the DB default).
    """
    row = ReportORM(
        id=report.id,
        case_id=report.case_id,
        title=report.title,
        format=report.format,
        content=report.content,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _report_to_domain(row)


def list_reports_for_case(db: Session, case_id: str) -> list[Report]:
    """List every report generated for a case, most recent first."""
    rows = db.query(ReportORM).filter(ReportORM.case_id == case_id).order_by(ReportORM.created_at.desc()).all()
    return [_report_to_domain(row) for row in rows]


def get_report(db: Session, report_id: str) -> Optional[Report]:
    """Look up a report by ID, or None."""
    row = db.get(ReportORM, report_id)
    return _report_to_domain(row) if row else None


def _access_request_to_domain(row: AccessRequestORM) -> AccessRequest:
    """Convert an AccessRequestORM row into a schema.access_request.AccessRequest."""
    return AccessRequest(
        id=row.id,
        requester_user_id=row.requester_user_id,
        target_case_id=row.target_case_id,
        matched_entity_id=row.matched_entity_id,
        reason=row.reason,
        status=AccessRequestStatus(row.status),
        created_at=row.created_at,
        escalated_at=row.escalated_at,
        resolved_at=row.resolved_at,
        resolved_by_user_id=row.resolved_by_user_id,
        denial_note=row.denial_note,
    )


def create_access_request(db: Session, access_request: AccessRequest) -> AccessRequest:
    """Insert a new access request (see schema.access_request for the
    escalation lifecycle) and return it as a domain dataclass.
    """
    row = AccessRequestORM(
        id=access_request.id,
        requester_user_id=access_request.requester_user_id,
        target_case_id=access_request.target_case_id,
        matched_entity_id=access_request.matched_entity_id,
        reason=access_request.reason,
        status=access_request.status.value,
        created_at=access_request.created_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _access_request_to_domain(row)


def get_access_request(db: Session, request_id: str) -> Optional[AccessRequest]:
    """Look up an access request by ID, or None."""
    row = db.get(AccessRequestORM, request_id)
    return _access_request_to_domain(row) if row else None


def get_access_requests_for_case(db: Session, case_id: str) -> list[AccessRequest]:
    """Every access request raised against a case, most recent first —
    what the case's investigators (and, once escalated, any Admin)
    see when reviewing pending requests.
    """
    rows = (
        db.query(AccessRequestORM)
        .filter(AccessRequestORM.target_case_id == case_id)
        .order_by(AccessRequestORM.created_at.desc())
        .all()
    )
    return [_access_request_to_domain(row) for row in rows]


def get_pending_admin_access_requests(db: Session) -> list[AccessRequest]:
    """Every access request currently escalated to PENDING_ADMIN,
    across every agency (per project decision, escalated requests are
    reviewable by any Admin/Super Admin regardless of agency — see
    schema.access_request's module docstring).
    """
    rows = (
        db.query(AccessRequestORM)
        .filter(AccessRequestORM.status == AccessRequestStatus.PENDING_ADMIN.value)
        .order_by(AccessRequestORM.created_at.asc())
        .all()
    )
    return [_access_request_to_domain(row) for row in rows]


def get_pending_investigator_access_requests_for_user(db: Session, user_id: str) -> list[AccessRequest]:
    """Every PENDING_INVESTIGATOR request targeting a case this user
    is an assigned investigator on — what that investigator sees as
    "awaiting your decision".
    """
    user_row = db.get(UserORM, user_id)
    if user_row is None:
        return []
    case_ids = {c.id for c in user_row.assigned_cases}
    if not case_ids:
        return []
    rows = (
        db.query(AccessRequestORM)
        .filter(
            AccessRequestORM.status == AccessRequestStatus.PENDING_INVESTIGATOR.value,
            AccessRequestORM.target_case_id.in_(case_ids),
        )
        .order_by(AccessRequestORM.created_at.asc())
        .all()
    )
    return [_access_request_to_domain(row) for row in rows]


def escalate_timed_out_access_requests(db: Session) -> list[AccessRequest]:
    """Lazily promote any PENDING_INVESTIGATOR request past its
    48-hour window (schema.access_request.ESCALATION_TIMEOUT) to
    PENDING_ADMIN. No background scheduler exists in this project, so
    this is called at the top of every route that lists/reads access
    requests (see api/routes/access_requests.py) rather than on a
    timer — a request can sit "stale" for a little longer than 48h if
    nobody looks at it, but it can never be silently missed, since
    every read path re-checks this first. Returns the requests that
    were just escalated by this call, for logging/notification.
    """
    pending_rows = (
        db.query(AccessRequestORM)
        .filter(AccessRequestORM.status == AccessRequestStatus.PENDING_INVESTIGATOR.value)
        .all()
    )
    escalated = []
    for row in pending_rows:
        domain = _access_request_to_domain(row)
        if domain.is_timed_out():
            row.status = AccessRequestStatus.PENDING_ADMIN.value
            row.escalated_at = domain.created_at  # overwritten with a real "now" below
            escalated.append(row)
    if escalated:
        now_iso = datetime.now(timezone.utc).isoformat()
        for row in escalated:
            row.escalated_at = now_iso
        db.commit()
        for row in escalated:
            db.refresh(row)
    return [_access_request_to_domain(row) for row in escalated]


def deny_access_request(db: Session, request_id: str, denied_by_user_id: str, note: Optional[str] = None) -> Optional[AccessRequest]:
    """An investigator denies a PENDING_INVESTIGATOR request — this
    escalates it to PENDING_ADMIN immediately (per project decision:
    an explicit deny is itself an escalation trigger, same as a
    timeout), rather than closing it out as a final DENIED. Only a
    PENDING_ADMIN denial (see resolve_access_request) is final.
    Returns None if request_id doesn't exist or isn't currently
    PENDING_INVESTIGATOR.
    """
    row = db.get(AccessRequestORM, request_id)
    if row is None or row.status != AccessRequestStatus.PENDING_INVESTIGATOR.value:
        return None
    row.status = AccessRequestStatus.PENDING_ADMIN.value
    row.escalated_at = datetime.now(timezone.utc).isoformat()
    row.denial_note = note
    db.commit()
    db.refresh(row)
    return _access_request_to_domain(row)


def resolve_access_request(db: Session, request_id: str, approve: bool, resolved_by_user_id: str) -> Optional[AccessRequest]:
    """Final resolution of an access request — approve or deny it.
    Callable at either PENDING_INVESTIGATOR (an investigator approving
    directly, with no need to escalate) or PENDING_ADMIN (an admin's
    final call after escalation). Returns None if request_id doesn't
    exist or is already resolved (APPROVED/DENIED).
    """
    row = db.get(AccessRequestORM, request_id)
    if row is None or row.status in (AccessRequestStatus.APPROVED.value, AccessRequestStatus.DENIED.value):
        return None
    row.status = AccessRequestStatus.APPROVED.value if approve else AccessRequestStatus.DENIED.value
    row.resolved_at = datetime.now(timezone.utc).isoformat()
    row.resolved_by_user_id = resolved_by_user_id
    db.commit()
    db.refresh(row)
    return _access_request_to_domain(row)


def grant_case_access(db: Session, case_id: str, user_id: str) -> Optional[Case]:
    """Give a user (typically an Investigator whose AccessRequest was
    just approved) scoped read access to a single case, by the same
    mechanism as assign_investigator — this project has no separate
    "read-only case access" relationship, so an approved request adds
    the requester as an assigned investigator on that one case. It
    does not grant them any broader visibility (e.g. it does not
    change their agency_id or role).
    """
    return assign_investigator(db, case_id, user_id)


def _ledger_entry_to_domain(row: LedgerEntryORM) -> LedgerEntry:
    """Convert a LedgerEntryORM row into a ledger.chain.LedgerEntry."""
    return LedgerEntry(
        id=row.id,
        sequence_number=row.sequence_number,
        event_type=LedgerEventType(row.event_type),
        payload=json.loads(row.payload),
        prev_hash=row.prev_hash,
        entry_hash=row.entry_hash,
        created_at=row.created_at,
    )


def get_last_ledger_entry(db: Session) -> Optional[LedgerEntry]:
    """The current tip of the chain (highest sequence_number), or
    None if the ledger has no entries at all (should only be true
    before ensure_genesis_entry has ever run).
    """
    row = db.query(LedgerEntryORM).order_by(LedgerEntryORM.sequence_number.desc()).first()
    return _ledger_entry_to_domain(row) if row else None


def ensure_genesis_entry(db: Session) -> LedgerEntry:
    """Idempotently seed the ledger's genesis entry (sequence_number=0)
    if the table is empty. Safe to call on every app startup (see
    api/main.py) — does nothing if a genesis entry already exists.
    """
    existing = get_last_ledger_entry(db)
    if existing is not None:
        return existing

    genesis = build_genesis_entry()
    row = LedgerEntryORM(
        id=genesis.id,
        sequence_number=genesis.sequence_number,
        event_type=genesis.event_type.value,
        payload=json.dumps(genesis.payload),
        prev_hash=genesis.prev_hash,
        entry_hash=genesis.entry_hash,
        created_at=genesis.created_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _ledger_entry_to_domain(row)


def append_ledger_entry(db: Session, event_type: LedgerEventType, payload: dict, max_retries: int = 20) -> LedgerEntry:
    """Append a new entry to the chain, computed against the CURRENT
    last entry. Callers: api/routes/cases.py (case_created),
    api/routes/query.py (evidence_linked — only for genuinely new
    links, see graph.explainability.link_evidence's return value),
    api/routes/access_requests.py (access_request_resolved).

    Calls ensure_genesis_entry first so this is always safe to call
    even if app startup's genesis seeding hasn't run for some reason
    (e.g. a test that creates its own fresh DB session) — belt and
    braces, since a missing genesis entry would otherwise make
    build_next_entry's very first call construct against a
    nonexistent "previous entry".

    Retries on a genuine race (two or more requests/threads appending
    concurrently — observed in production: two /query/{case_id}
    requests each calling this for different newly-linked entities at
    close enough to the same time that both read the same "last
    entry" before either committed). The DB's unique constraint on
    sequence_number correctly rejects every loser as an
    IntegrityError; this function catches that, rolls back, waits a
    short randomized backoff, re-reads the now-current last entry,
    and rebuilds.

    The backoff is NOT optional under tight contention: an earlier
    version of this function retried immediately with no delay, which
    is insufficient when 3+ callers race in the same instant — all of
    them can re-read the same stale "last entry" again on their very
    next attempt (nothing forces them apart in time) and collide
    again, repeatedly, exactly as seen in real testing (3 threads all
    failing on sequence_number=94 simultaneously, with zero
    successful retries). A randomized delay that grows with the
    attempt number (indexed by this call's own random jitter, not
    synchronized across threads) breaks that lockstep by giving
    different callers different wait times, so they stop colliding
    with each other run after run.

    Raises the IntegrityError only if max_retries is exhausted, which
    at this point would indicate something more persistently wrong
    than ordinary contention (e.g. a caller stuck in a tight loop
    outnumbering max_retries, or a real DB-level problem).
    """
    for attempt in range(max_retries):
        if attempt > 0:
            # Randomized, growing backoff — see docstring for why a
            # fixed or zero delay isn't enough under real contention.
            # Capped at ~1.5s so a legitimate multi-way race still
            # resolves quickly rather than visibly stalling a request.
            # (Raised from an earlier 0.5s cap: a remote DB connection
            # — e.g. Neon — has real network round-trip latency on
            # every read/write, which widens the actual race window
            # beyond what a same-machine/local DB test shows, so more
            # headroom is needed for retries to reliably win.)
            backoff_seconds = min(1.5, (0.02 * (2 ** attempt))) * (0.5 + random.random())
            time.sleep(backoff_seconds)

        previous = get_last_ledger_entry(db)
        if previous is None:
            previous = ensure_genesis_entry(db)
        new_entry = build_next_entry(previous, event_type, payload)

        row = LedgerEntryORM(
            id=new_entry.id,
            sequence_number=new_entry.sequence_number,
            event_type=new_entry.event_type.value,
            payload=json.dumps(new_entry.payload),
            prev_hash=new_entry.prev_hash,
            entry_hash=new_entry.entry_hash,
            created_at=new_entry.created_at,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if attempt == max_retries - 1:
                raise RuntimeError(
                    f"append_ledger_entry exhausted all {max_retries} retries "
                    f"(last attempted sequence_number={new_entry.sequence_number}) — "
                    f"this means contention is higher than max_retries/backoff can "
                    f"currently absorb, not that the retry logic itself is broken."
                ) from None
            continue
        db.refresh(row)
        return _ledger_entry_to_domain(row)


def get_all_ledger_entries(db: Session) -> list[LedgerEntry]:
    """Every ledger entry, ordered by sequence_number — the full chain,
    for ledger.chain.verify_chain to walk.
    """
    rows = db.query(LedgerEntryORM).order_by(LedgerEntryORM.sequence_number.asc()).all()
    return [_ledger_entry_to_domain(row) for row in rows]


# ---------------------------------------------------------------------------
# Evidence links (persisted explainability trail)
# ---------------------------------------------------------------------------

def link_evidence_persistent(db: Session, entity_id: str, document_id: str, case_id: str) -> bool:
    """Record that a resolved entity is backed by a source document.
    Idempotent. Returns True only when the link is NEW (the caller
    appends a ledger entry only then, so re-querying a case never
    re-writes history).
    """
    exists = (
        db.query(EvidenceLinkORM.id)
        .filter(EvidenceLinkORM.entity_id == entity_id, EvidenceLinkORM.document_id == document_id)
        .first()
    )
    if exists:
        return False
    db.add(EvidenceLinkORM(id=str(uuid.uuid4()), entity_id=entity_id, document_id=document_id, case_id=case_id))
    try:
        db.commit()
    except IntegrityError:  # lost a race with another request: link now exists
        db.rollback()
        return False
    return True


def get_evidence_for_entity(db: Session, entity_id: str) -> tuple:
    """(case_ids, [SourceDocument]) backing a resolved entity id."""
    rows = db.query(EvidenceLinkORM).filter(EvidenceLinkORM.entity_id == entity_id).all()
    case_ids = {r.case_id for r in rows}
    docs = []
    for r in rows:
        doc = get_document(db, r.document_id)
        if doc is not None:
            docs.append(doc)
    return case_ids, docs


# ---------------------------------------------------------------------------
# Resolution overrides (investigator review decisions)
# ---------------------------------------------------------------------------

def add_resolution_override(db: Session, case_id: str, mention_a_id: str, mention_b_id: str,
                            action: str, created_by_user_id: Optional[str], note: Optional[str] = None) -> dict:
    """Store "never_merge"/"force_merge" for a mention pair. A newer
    decision on the same pair replaces the older one."""
    if action not in ("never_merge", "force_merge"):
        raise ValueError("action must be 'never_merge' or 'force_merge'")
    a, b = sorted((mention_a_id, mention_b_id))
    row = (
        db.query(ResolutionOverrideORM)
        .filter_by(case_id=case_id, mention_a_id=a, mention_b_id=b)
        .first()
    )
    if row is None:
        row = ResolutionOverrideORM(id=str(uuid.uuid4()), case_id=case_id, mention_a_id=a, mention_b_id=b,
                                    action=action, note=note, created_by_user_id=created_by_user_id)
        db.add(row)
    else:
        row.action, row.note, row.created_by_user_id = action, note, created_by_user_id
        row.created_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    return _override_to_dict(row)


def _override_to_dict(row: ResolutionOverrideORM) -> dict:
    return {"id": row.id, "case_id": row.case_id, "mention_a_id": row.mention_a_id,
            "mention_b_id": row.mention_b_id, "action": row.action, "note": row.note,
            "created_by_user_id": row.created_by_user_id, "created_at": row.created_at}


def list_resolution_overrides(db: Session, case_id: str) -> list:
    rows = db.query(ResolutionOverrideORM).filter_by(case_id=case_id).order_by(ResolutionOverrideORM.created_at).all()
    return [_override_to_dict(r) for r in rows]


def get_resolution_overrides(db: Session, case_id: str) -> dict:
    """Overrides in the shape nlp.resolution.resolve_entities expects."""
    out = {"never_merge": [], "force_merge": []}
    for r in db.query(ResolutionOverrideORM).filter_by(case_id=case_id).all():
        out[r.action].append((r.mention_a_id, r.mention_b_id))
    return out


def delete_resolution_override(db: Session, case_id: str, override_id: str) -> bool:
    row = db.query(ResolutionOverrideORM).filter_by(case_id=case_id, id=override_id).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Audit log (append-only)
# ---------------------------------------------------------------------------

def append_audit(db: Session, action: str, *, actor: Optional[User] = None, actor_badge_id: Optional[str] = None,
                 target_type: Optional[str] = None, target_id: Optional[str] = None,
                 detail: Optional[dict] = None, ip_address: Optional[str] = None, success: bool = True) -> None:
    """Record a security-relevant action. Never raises: an audit-write
    failure must not turn a legitimate request into a 500 (it is logged
    to the server log instead)."""
    try:
        db.add(AuditLogORM(
            id=str(uuid.uuid4()),
            actor_user_id=actor.id if actor else None,
            actor_badge_id=(actor.badge_id if actor else actor_badge_id),
            action=action, target_type=target_type, target_id=target_id,
            detail=json.dumps(detail, default=str) if detail else None,
            ip_address=ip_address, success="true" if success else "false",
        ))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        import logging
        logging.getLogger(__name__).exception("audit log write failed for action=%s", action)


def list_audit(db: Session, limit: int = 100, offset: int = 0, action: Optional[str] = None) -> list:
    q = db.query(AuditLogORM)
    if action:
        q = q.filter(AuditLogORM.action == action)
    rows = q.order_by(AuditLogORM.created_at.desc()).offset(offset).limit(min(limit, 500)).all()
    return [
        {"id": r.id, "created_at": r.created_at, "actor_user_id": r.actor_user_id,
         "actor_badge_id": r.actor_badge_id, "action": r.action, "target_type": r.target_type,
         "target_id": r.target_id, "detail": json.loads(r.detail) if r.detail else None,
         "ip_address": r.ip_address, "success": r.success == "true"}
        for r in rows
    ]

