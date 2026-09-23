"""
api/routes/query.py

Retrieve a case's graph for the dashboard, plus the entity-resolution
review workflow.

Authorization: every route checks case access (403 otherwise); the
override routes need case WRITE access (assigned investigator).

The graph is built at query time by api.casegraph.build_case_graph:
persisted mentions -> nlp.resolution (multi-signal, with the case's
investigator overrides applied) -> graph.build. Resolution runs at query
time on purpose: it needs the full cross-document mention set, and
re-running keeps ingestion append-only with no invalidation logic.

Stable ids + persisted evidence: resolved-entity ids are a hash of their
member mention ids (nlp.resolution), so the same evidence gives the same
node id on every request. _link_evidence_for_resolved_entities persists
(entity, document) links to the evidence_links table, and appends a
ledger entry only for links that are genuinely new -- re-loading a case
no longer writes to the ledger (previously every page load minted fresh
random ids and therefore fresh "new" ledger entries).

Response shape matches dashboard/src/sampleData.js's SAMPLE_GRAPH
(caseId/nodes/edges/stats), extended with per-node review metadata:
needsReview, mergeConfidence, mergeReasons, aliases, members.

Honest 501 (not a fabricated empty graph) when a case has no persisted
entities yet.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import graph.analytics as graph_analytics
import graph.build as graph_build
from api.auth import get_current_user
from api.casegraph import build_case_graph
from api.permissions import require_case_read, require_case_write
from db import repository as repo
from db.connection import get_db
from ledger.chain import LedgerEventType
from schema.user import User

router = APIRouter()


def _link_evidence_for_resolved_entities(db: Session, case_id: str, resolved_entities: list) -> None:
    """Persist entity -> source-document links; ledger only new ones."""
    for entity in resolved_entities:
        for doc_id in entity.source_doc_ids:
            if repo.link_evidence_persistent(db, entity.id, doc_id, case_id):
                repo.append_ledger_entry(
                    db,
                    LedgerEventType.EVIDENCE_LINKED,
                    {"entity_id": entity.id, "document_id": doc_id, "case_id": case_id},
                )


def _get_authorized_case_graph(db: Session, user: User, case_id: str) -> tuple:
    """authorize-then-build shared by the graph and link-prediction
    routes. Returns (graph, resolved_entities)."""
    require_case_read(db, user, case_id)
    graph, resolved_entities, _ = build_case_graph(db, case_id)
    if graph is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Authorized, but this case has no persisted entities/relationships "
                "to build a graph from yet (ingest a document first via api/routes/ingestion.py)."
            ),
        )
    return graph, resolved_entities


@router.get("/{case_id}")
def query_endpoint(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Return the resolved graph for a case (403 if not authorized, 501
    if nothing has been ingested yet)."""
    graph, resolved_entities = _get_authorized_case_graph(db, user, case_id)
    _link_evidence_for_resolved_entities(db, case_id, resolved_entities)

    nodes = [
        {
            "id": node_id,
            "label": attrs["canonical_text"],
            "entity_type": attrs["entity_type"].lower(),
            "needsReview": bool(attrs.get("needs_review")),
            "mergeConfidence": attrs.get("merge_confidence", 1.0),
            "mergeReasons": attrs.get("merge_reasons", []),
            "aliases": attrs.get("aliases", []),
            "members": attrs.get("members", []),
            "documentCount": len(attrs.get("source_doc_ids", [])),
        }
        for node_id, attrs in graph.nodes(data=True)
    ]
    edges = [
        {
            "id": f"{source}-{target}-{key}",
            "source": source,
            "target": target,
            "relationship_type": attrs["relation_type"].replace("-", "_"),
            "weight": attrs.get("confidence", 1.0),
            "evidenceCount": len(attrs.get("evidence", [])),
        }
        for source, target, key, attrs in graph.edges(keys=True, data=True)
    ]

    influencers = graph_build.rank_influencers(graph, top_n=5)
    influencer = influencers[0] if influencers else None
    rank_of = {i["node_id"]: i["rank"] for i in influencers}
    for node in nodes:
        node["influencerRank"] = rank_of.get(node["id"])
    review_count = sum(1 for n in nodes if n["needsReview"])

    return {
        "caseId": case_id,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "entitiesLinked": graph.number_of_nodes(),
            "keyInfluencers": len(influencers),
            "flaggedPatterns": 0,  # real anomaly counts are served by api/routes/alerts.py
            "needsReview": review_count,
        },
        "influencer": influencer,
        "influencers": influencers,
    }


@router.get("/{case_id}/link-predictions")
def link_predictions_endpoint(
    case_id: str, top_n: int = 10, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    """Likely-but-unrecorded relationships within this case's graph
    (structural leads for an investigator to verify, not edges)."""
    graph, _ = _get_authorized_case_graph(db, user, case_id)
    predictions = graph_analytics.compute_link_predictions(graph, top_n=top_n)

    label_by_id = {node_id: attrs["canonical_text"] for node_id, attrs in graph.nodes(data=True)}
    for prediction in predictions:
        prediction["entity_a_label"] = label_by_id.get(prediction["entity_a_id"], prediction["entity_a_id"])
        prediction["entity_b_label"] = label_by_id.get(prediction["entity_b_id"], prediction["entity_b_id"])

    return {"caseId": case_id, "predictions": predictions}


# ---------------------------------------------------------------------------
# Entity-resolution review workflow
# ---------------------------------------------------------------------------

@router.get("/{case_id}/resolution/overrides")
def list_overrides_endpoint(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    require_case_read(db, user, case_id)
    return {"overrides": repo.list_resolution_overrides(db, case_id)}


@router.post("/{case_id}/resolution/overrides", status_code=status.HTTP_201_CREATED)
def add_override_endpoint(
    case_id: str, data: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    """Record an investigator decision on two entity mentions.

    Body: {"mention_a_id", "mention_b_id", "action": "never_merge" |
    "force_merge", "note"?}. Both mentions must belong to this case.
    Use it to SPLIT a wrong merge (never_merge) or CONFIRM a missed one
    (force_merge). The graph is rebuilt on the next query.
    """
    require_case_write(db, user, case_id)
    a, b, action = data.get("mention_a_id"), data.get("mention_b_id"), data.get("action")
    if not a or not b or a == b:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="two different mention ids are required")
    if action not in ("never_merge", "force_merge"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="action must be 'never_merge' or 'force_merge'")

    by_id = {m.id: m for m in repo.get_entities_for_case(db, case_id)}
    if a not in by_id or b not in by_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Both mentions must belong to this case")
    if by_id[a].entity_type != by_id[b].entity_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Both mentions must be the same kind of entity")
    if by_id[a].entity_type.value in ("PHONE", "VEHICLE", "ACCOUNT"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("Identical phone numbers, vehicle registrations and accounts are always one entity. "
                    "To separate two people who share one, split the PERSON nodes instead."),
        )

    override = repo.add_resolution_override(db, case_id, a, b, action, user.id, data.get("note"))
    repo.append_audit(db, f"resolution_{action}", actor=user, target_type="case", target_id=case_id,
                      detail={"mention_a_id": a, "mention_b_id": b, "note": data.get("note")})
    return override


@router.delete("/{case_id}/resolution/overrides/{override_id}")
def delete_override_endpoint(
    case_id: str, override_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    require_case_write(db, user, case_id)
    if not repo.delete_resolution_override(db, case_id, override_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Override not found")
    repo.append_audit(db, "resolution_override_removed", actor=user, target_type="case", target_id=case_id,
                      detail={"override_id": override_id})
    return {"status": "deleted"}
