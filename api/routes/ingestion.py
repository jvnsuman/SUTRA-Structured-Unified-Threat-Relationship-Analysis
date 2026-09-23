"""
api/routes/ingestion.py

Accepts multi-source case data (FIRs, CDRs, financial records, ...) and
runs it through nlp.pipeline.process_document -- the single shared
document->entities+relations pipeline (also used by the seed scripts).

Authorization (new): ingesting CHANGES a case, so it requires case WRITE
access (an assigned investigator, or a super-admin) -- analysts are
read-only and admins manage accounts, not case content. Previously any
authenticated user could ingest into any case_id. A document with no
case_id is rejected for the same reason (it would be orphaned and
invisible to everyone).

Structured input: for document_type "cdr" / "financial" the request may
carry `structured` (see nlp/structured.py for the accepted shape); it is
parsed directly into entities/relations and also stored for the anomaly
detectors (graph.analytics). raw_text may then be a short summary.

Relations are rule-based by default (nlp/relation_rules.py): no
torch/transformers needed, so relations are always produced. Set
USE_ZERO_SHOT_RELATIONS=1 to add the NLI model on top.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.permissions import require_case_read, require_case_write
from db import repository as repo
from db.connection import get_db
from nlp.pipeline import process_document
from schema.entities import SourceDocument
from schema.user import User

router = APIRouter()

_MAX_TEXT_CHARS = 200_000
_MAX_STRUCTURED_ROWS = 20_000


@router.post("/", status_code=status.HTTP_202_ACCEPTED)
def ingest_endpoint(data: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Validate, authorize, persist and process one document.
    Returns 202 either way: the document was stored whether or not
    extraction could run."""
    try:
        document = SourceDocument.from_dict(data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if not document.case_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="case_id is required")
    require_case_write(db, user, document.case_id)

    if len(document.raw_text) > _MAX_TEXT_CHARS:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail=f"raw_text is limited to {_MAX_TEXT_CHARS:,} characters")
    structured = document.structured or {}
    rows = len(structured.get("calls", []) or []) + len(structured.get("transactions", []) or [])
    if rows > _MAX_STRUCTURED_ROWS:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail=f"structured data is limited to {_MAX_STRUCTURED_ROWS:,} rows")

    if repo.get_document(db, document.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A document with id {document.id!r} has already been ingested",
        )
    repo.create_document(db, document)
    repo.append_audit(db, "document_ingested", actor=user, target_type="case", target_id=document.case_id,
                      detail={"document_id": document.id, "document_type": document.document_type})

    try:
        result = process_document(document)
    except RuntimeError as exc:
        # spaCy (or its model) isn't available: the document is stored anyway.
        return {
            "document_id": document.id,
            "case_id": document.case_id,
            "status": "stored_pending_extraction",
            "detail": f"Document accepted and stored. Entity extraction unavailable: {exc}",
        }

    repo.create_entities(db, result.entities)
    repo.create_relationships(db, result.relations)

    response = {
        "document_id": document.id,
        "case_id": document.case_id,
        "status": "extracted",
        "entity_count": len(result.entities),
        "relation_count": len(result.relations),
    }
    if result.warnings:
        response["detail"] = "; ".join(result.warnings[:5])
        response["warnings"] = result.warnings
    return response


@router.get("/{case_id}/summary")
def document_summary_endpoint(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Per-document-type counts and last-ingested timestamps for a case
    (backs the Data Sources page)."""
    require_case_read(db, user, case_id)
    return {"sources": repo.get_document_summary_for_case(db, case_id)}