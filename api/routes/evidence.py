"""
api/routes/evidence.py

Serves evidence-trail data to the dashboard's EvidencePanel -- the source
documents backing a resolved entity, for explainability.

Links are persisted (db.models.EvidenceLinkORM) when a case is queried
(api/routes/query.py), so they survive restarts. Authorization: an
entity's evidence is only returned to a user who can read the case the
links belong to. Previously ANY logged-in user could read the source
documents behind any entity id from any case (there was no entity->case
link to check); persisted links carry case_id, which closes that.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import get_current_user
from db import repository as repo
from db.connection import get_db
from schema.user import User

router = APIRouter()


@router.get("/{entity_id}")
def evidence_endpoint(entity_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Evidence trail (source documents) for a resolved entity.
    403 if the entity belongs to a case the caller cannot read; an empty
    list (200) if the entity has no links yet."""
    case_ids, docs = repo.get_evidence_for_entity(db, entity_id)
    visible = {c.id for c in repo.get_cases_for_user(db, user)}
    if case_ids and not (case_ids & visible):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not authorized to view this evidence")
    return {
        "entity_id": entity_id,
        "case_id": next(iter(case_ids), None),
        "evidence": [doc.to_dict() for doc in docs],
    }
