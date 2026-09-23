"""
db/models.py

SQLAlchemy ORM models — the persisted (table) shape of the domain
dataclasses defined in schema/user.py, schema/case.py, and
schema/entities.py. db/repository.py is the adapter that converts
between these ORM rows and those dataclasses; nothing outside db/
should import from this module directly.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, String, Table, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models below."""


def _utc_now_iso() -> str:
    """Column default for created_at fields — a plain ISO-8601 UTC
    timestamp string, matching the style already used for
    UserORM.last_login and CaseORM.opened_at elsewhere in this file.
    """
    return datetime.now(timezone.utc).isoformat()


# Many-to-many join table between cases and their assigned investigators.
case_investigators = Table(
    "case_investigators",
    Base.metadata,
    Column("case_id", String, ForeignKey("cases.id"), primary_key=True),
    Column("user_id", String, ForeignKey("users.id"), primary_key=True),
)


class AgencyORM(Base):
    """Table form of schema.user.Agency."""

    __tablename__ = "agencies"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    agency_type = Column(String, nullable=False)
    parent_agency_id = Column(String, ForeignKey("agencies.id"), nullable=True)
    is_active = Column(String, default="true")  # "true"/"false" string, matched at the repository layer

    users = relationship("UserORM", back_populates="agency")
    cases = relationship("CaseORM", back_populates="agency")


class UserORM(Base):
    """Table form of schema.user.User."""

    __tablename__ = "users"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    badge_id = Column(String, unique=True, nullable=False, index=True)
    agency_id = Column(String, ForeignKey("agencies.id"), nullable=False)
    role = Column(String, nullable=False)  # schema.user.Role value
    password_hash = Column(String, nullable=True)
    last_login = Column(String, nullable=True)
    preferences = Column(String, nullable=True)  # JSON-encoded dict — see schema.user.User.preferences

    agency = relationship("AgencyORM", back_populates="users")
    assigned_cases = relationship("CaseORM", secondary=case_investigators, back_populates="investigators")


class CaseORM(Base):
    """Table form of schema.case.Case."""

    __tablename__ = "cases"

    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    agency_id = Column(String, ForeignKey("agencies.id"), nullable=False)
    description = Column(String, nullable=True, default="")
    status = Column(String, nullable=False, default="open")  # schema.case.CaseStatus value
    confidentiality = Column(String, nullable=False, default="normal")  # schema.case.CaseConfidentiality value
    created_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    opened_at = Column(String, nullable=False)

    agency = relationship("AgencyORM", back_populates="cases")
    investigators = relationship("UserORM", secondary=case_investigators, back_populates="assigned_cases")
    documents = relationship("SourceDocumentORM", back_populates="case")


class SourceDocumentORM(Base):
    """Table form of schema.entities.SourceDocument."""

    __tablename__ = "source_documents"

    id = Column(String, primary_key=True)
    document_type = Column(String, nullable=False)  # one of schema.entities.VALID_DOCUMENT_TYPES
    raw_text = Column(String, nullable=False)
    case_id = Column(String, ForeignKey("cases.id"), nullable=True)
    created_at = Column(String, nullable=False, default=_utc_now_iso)  # backs db.repository.get_document_summary_for_case
    # JSON-encoded dict of CDR/financial structured data ("calls" or
    # "transactions" lists — see data/generate_synthetic.py's
    # SyntheticDocument for the exact shape). Same encode-as-string
    # pattern as UserORM.preferences. Null/empty for FIR/surveillance/
    # etc documents, which have no structured equivalent. Feeds
    # graph.analytics.detect_anomalies's financial-structuring and
    # communication-burst checks (see api/routes/alerts.py's docstring
    # for the gap this closes — those two checks previously only ever
    # fired against synthetic documents, never real ingested ones).
    structured = Column(String, nullable=True)

    case = relationship("CaseORM", back_populates="documents")
    entities = relationship("EntityORM", back_populates="source_document")


class EntityORM(Base):
    """Table form of schema.entities.Entity. Not yet written to by any
    route — reserved for when nlp/extraction.py starts producing real
    entities to persist.
    """

    __tablename__ = "entities"

    id = Column(String, primary_key=True)
    entity_type = Column(String, nullable=False)  # schema.entities.EntityType value
    raw_text = Column(String, nullable=False)
    normalized_text = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    source_document_id = Column(String, ForeignKey("source_documents.id"), nullable=True)
    # JSON-encoded ExtractedEntity.metadata (engine, alias_group,
    # nearby_ids, script, start_char/end_char ...). Resolution reads
    # alias_group and nearby_ids from here -- without persisting them the
    # multi-signal resolver would have nothing but bare names to work on.
    metadata_json = Column(String, nullable=True)

    source_document = relationship("SourceDocumentORM", back_populates="entities")


class RelationshipORM(Base):
    """Table form of schema.entities.Relationship. Not yet written to
    by any route — reserved for when graph/build.py starts persisting
    edges.
    """

    __tablename__ = "relationships"

    id = Column(String, primary_key=True)
    source_entity_id = Column(String, ForeignKey("entities.id"), nullable=False)
    target_entity_id = Column(String, ForeignKey("entities.id"), nullable=False)
    relationship_type = Column(String, nullable=False)  # schema.entities.RelationshipType value
    source_document_id = Column(String, ForeignKey("source_documents.id"), nullable=True)
    weight = Column(Float, nullable=False, default=1.0)


class ReportORM(Base):
    """Table form of schema.report.Report — a generated case summary
    (Markdown) or data export (CSV), persisted so the Reports page can
    list and re-download past reports instead of regenerating them
    on every visit.
    """

    __tablename__ = "reports"

    id = Column(String, primary_key=True)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    title = Column(String, nullable=False)
    format = Column(String, nullable=False)  # schema.report.VALID_REPORT_FORMATS value
    content = Column(String, nullable=False)
    created_at = Column(String, nullable=False, default=_utc_now_iso)


class AccessRequestORM(Base):
    """Table form of schema.access_request.AccessRequest — see that
    module's docstring for the full escalation lifecycle.
    """

    __tablename__ = "access_requests"

    id = Column(String, primary_key=True)
    requester_user_id = Column(String, ForeignKey("users.id"), nullable=False)
    target_case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    matched_entity_id = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending_investigator")  # schema.access_request.AccessRequestStatus value
    created_at = Column(String, nullable=False, default=_utc_now_iso)
    escalated_at = Column(String, nullable=True)
    resolved_at = Column(String, nullable=True)
    resolved_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    denial_note = Column(String, nullable=True)


class LedgerEntryORM(Base):
    """Table form of ledger.chain.LedgerEntry — the hash-chained,
    append-only integrity ledger. See ledger/chain.py's module
    docstring for the tamper-evidence design. sequence_number and
    entry_hash are both unique: sequence_number enforces there is
    exactly one entry per position in the chain (no gaps, no
    duplicates at write time — DB-level defense-in-depth alongside
    the hash-chain check itself), and entry_hash being unique means
    two entries can never accidentally collide.

    No update or delete path exists anywhere in db.repository for
    this table, by design — an append-only table with no ORM-level
    update method is the actual enforcement of "append-only" at the
    application layer (a DB admin with raw SQL access could still
    edit rows directly, which is exactly the tampering
    ledger.chain.verify_chain is built to detect after the fact).
    """

    __tablename__ = "ledger_entries"

    id = Column(String, primary_key=True)
    sequence_number = Column(Integer, nullable=False, unique=True)
    event_type = Column(String, nullable=False)  # ledger.chain.LedgerEventType value
    payload = Column(String, nullable=False)  # JSON-encoded dict, same encode-as-string pattern as UserORM.preferences
    prev_hash = Column(String, nullable=False)
    entry_hash = Column(String, nullable=False, unique=True)
    created_at = Column(String, nullable=False, default=_utc_now_iso)


class EvidenceLinkORM(Base):
    """Persisted explainability link: resolved entity -> source document.

    Replaces the old in-memory dict (lost on every restart, and shared
    across users with no case check). entity_id is a deterministic
    ResolvedEntity.id (hash of member mention ids -- see nlp.resolution);
    case_id lets the evidence endpoint enforce the same case
    authorization the graph endpoint does.
    """

    __tablename__ = "evidence_links"
    __table_args__ = (
        UniqueConstraint("entity_id", "document_id", name="uq_evidence_entity_document"),
        Index("ix_evidence_links_entity_id", "entity_id"),
    )

    id = Column(String, primary_key=True)
    entity_id = Column(String, nullable=False)
    document_id = Column(String, ForeignKey("source_documents.id"), nullable=False)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, index=True)
    created_at = Column(String, nullable=False, default=_utc_now_iso)


class ResolutionOverrideORM(Base):
    """An investigator's decision about two entity mentions: "never_merge"
    ("these are different people") or "force_merge" ("these are the same").
    Applied by api/routes/query.py on every resolution run for the case.
    """

    __tablename__ = "resolution_overrides"
    __table_args__ = (UniqueConstraint("case_id", "mention_a_id", "mention_b_id", name="uq_override_pair"),)

    id = Column(String, primary_key=True)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, index=True)
    mention_a_id = Column(String, nullable=False)
    mention_b_id = Column(String, nullable=False)
    action = Column(String, nullable=False)  # "never_merge" | "force_merge"
    note = Column(String, nullable=True)
    created_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(String, nullable=False, default=_utc_now_iso)


class AuditLogORM(Base):
    """Who did what, when -- security-relevant actions (logins, failed
    logins, user/role/agency/case-access changes, overrides, exports).
    Append-only at the application layer (no update/delete in
    db.repository). Complements the hash-chained ledger, which covers
    evidence integrity; this covers administrative and access activity.
    """

    __tablename__ = "audit_log"

    id = Column(String, primary_key=True)
    created_at = Column(String, nullable=False, default=_utc_now_iso, index=True)
    actor_user_id = Column(String, nullable=True)
    actor_badge_id = Column(String, nullable=True)
    action = Column(String, nullable=False, index=True)
    target_type = Column(String, nullable=True)
    target_id = Column(String, nullable=True)
    detail = Column(String, nullable=True)   # JSON-encoded dict
    ip_address = Column(String, nullable=True)
    success = Column(String, nullable=False, default="true")  # "true"/"false"

