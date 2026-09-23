"""
api/routes/cross_case.py

Cross-case entity match panel — deliberately a SEPARATE surface from
the main per-case graph (api/routes/query.py), not merged into it.
When an entity in the current case also appears in another case the
viewer is NOT authorized for, this shows a confidentiality-gated
summary of that other case plus a "Request Access" action, instead of
either leaking its contents or silently hiding that a match exists.

Confidentiality gating (schema.case.CaseConfidentiality — settable
only by ADMIN/SUPER_ADMIN via api/routes/cases.py):
  NORMAL:     title + description + owning agency name are shown.
  RESTRICTED: ONLY the owning agency name is shown. No title, no
              description. Intended for highly confidential or
              national-level-risk cases.

Matching reuses nlp.resolution's pairwise primitives (compute_similarity /
resolve_match) directly on ExtractedEntity mentions — it does not
introduce a second matching algorithm. Only entities the viewer is
NOT already authorized to see are compared; cases already visible to
the user via db.repository.get_cases_for_user are excluded, since
there is nothing to request access to.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import get_current_user
from db import repository as repo
from db.connection import get_db
from collections import defaultdict

from nlp.resolution import blocking_keys, compute_similarity, resolve_match
from schema.access_request import new_access_request
from schema.case import CaseConfidentiality
from schema.user import Role, User

router = APIRouter()


def _case_summary_for_viewer(case, agency_name: str) -> dict:
    """The confidentiality-gated view of a case that the CURRENT
    (unauthorized) viewer is allowed to see — see module docstring.
    """
    if case.confidentiality == CaseConfidentiality.RESTRICTED:
        return {
            "case_id": case.id,
            "confidentiality": case.confidentiality.value,
            "agency_name": agency_name,
        }
    return {
        "case_id": case.id,
        "confidentiality": case.confidentiality.value,
        "agency_name": agency_name,
        "title": case.title,
        "description": case.description,
    }


@router.get("/{case_id}/cross-case-matches")
def get_cross_case_matches(
    case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    """For every entity in `case_id`, find matches in cases the viewer
    is NOT authorized to see, and return one match summary per pair
    (this case's entity, the other case's confidentiality-gated
    summary). Does not compare against cases the viewer can already
    see in full via /cases/ — those aren't cross-case matches from
    this viewer's point of view, just entities in cases they already
    have.

    Each other case's entities are indexed by nlp.resolution's blocking
    keys (phonetic name tokens / exact identifier / shared-identifier
    keys), so an entity is only compared with the few candidates that
    share a key -- roughly O(entities x candidates) instead of the
    O(entities x entities) full pairwise scan this used to do.
    """
    visible_case_ids = {c.id for c in repo.get_cases_for_user(db, user)}
    if case_id not in visible_case_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not authorized to view this case")

    this_case_entities = [e for e in repo.get_entities_for_case(db, case_id) if e.normalized_text]

    # SUPER_ADMIN can already see every case, so they can never have a
    # cross-case match against a case they're unauthorized for — skip
    # the scan entirely rather than run it against an empty "other
    # cases" set.
    if user.role == Role.SUPER_ADMIN:
        return {"matches": []}

    other_case_rows = repo.get_all_cases_except(db, exclude_case_ids=visible_case_ids)

    matches = []
    for other_case in other_case_rows:
        other_entities = [e for e in repo.get_entities_for_case(db, other_case.id) if e.normalized_text]
        if not other_entities:
            continue

        agency = repo.get_agency(db, other_case.agency_id)
        agency_name = agency.name if agency else other_case.agency_id

        index: dict = defaultdict(dict)
        for other_entity in other_entities:
            for key in blocking_keys(other_entity):
                index[key][other_entity.id] = other_entity

        seen_pairs = set()  # (this_entity.id, other_case.id) — one match summary per entity per other case, not per mention pair
        for this_entity in this_case_entities:
            candidates: dict = {}
            for key in blocking_keys(this_entity):
                candidates.update(index.get(key, {}))
            for other_entity in candidates.values():
                if this_entity.entity_type != other_entity.entity_type:
                    continue
                if not resolve_match(this_entity, other_entity):
                    continue

                pair_key = (this_entity.id, other_case.id)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                matches.append(
                    {
                        "matched_entity_id": this_entity.id,
                        "matched_entity_text": this_entity.normalized_text,
                        "matched_entity_type": this_entity.entity_type.value,
                        "similarity": compute_similarity(this_entity, other_entity),
                        "other_case": _case_summary_for_viewer(other_case, agency_name),
                    }
                )

    return {"matches": matches}


@router.post("/{case_id}/cross-case-matches/{matched_entity_id}/request-access", status_code=status.HTTP_201_CREATED)
def request_cross_case_access(
    case_id: str,
    matched_entity_id: str,
    data: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Raise an AccessRequest for the OTHER case referenced by a
    cross-case match (see schema.access_request for the full
    escalation lifecycle: pending_investigator -> pending_admin ->
    approved/denied). `case_id`/`matched_entity_id` here identify the
    match the request came from, for the requester's own audit trail —
    the actual case being requested is `data["target_case_id"]`,
    returned by get_cross_case_matches as other_case.case_id.
    """
    target_case_id = data.get("target_case_id")
    if not target_case_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="target_case_id is required")

    reason = data.get("reason")
    if not reason or not str(reason).strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="reason is required")

    visible_case_ids = {c.id for c in repo.get_cases_for_user(db, user)}
    if target_case_id in visible_case_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already have access to this case",
        )

    target_case = repo.get_case(db, target_case_id)
    if target_case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target case not found")

    access_request = new_access_request(
        requester_user_id=user.id,
        target_case_id=target_case_id,
        matched_entity_id=matched_entity_id,
        reason=str(reason).strip(),
    )
    created = repo.create_access_request(db, access_request)
    return created.to_dict()
