"""
graph/explainability.py

Evidence-trail layer — makes every flagged node/edge traceable back to
the source document(s) that justify it. This is what turns a "risk
score" into something court-usable: decision support, not decision-
making — the investigator makes the final call, with the evidence in
front of them.

Distinct from graph.build.get_evidence_trail(graph, node_id), which
reads attributes off one already-built graph object. This module is a
system-wide, ID-keyed index that survives independently of any
specific graph instance — the same entity_id can be looked up here
regardless of which graph build it appeared in.

Status: in-memory reference store (same pattern as schema/user.py's
stores) — not persisted across a restart. Populated by
api/routes/query.py on every query (see that module's
_link_evidence_for_resolved_entities); link_evidence's own idempotency
check means repeat calls for the same (entity_id, document.id) pair
are cheap no-ops, not duplicate work.

link_evidence returns whether it actually recorded something NEW —
api/routes/query.py uses this to append a ledger entry (ledger/chain.py)
only for genuinely new evidence links, not on every repeat query.
"""

from schema.entities import SourceDocument

_EVIDENCE_STORE: dict[str, list[SourceDocument]] = {}


def link_evidence(node_or_edge_id: str, source_document: SourceDocument) -> bool:
    """Record that a graph node or edge is backed by a specific source
    document. Idempotent per (id, document.id) pair — linking the same
    document to the same node twice does not duplicate it.

    Returns:
        True if this call actually added a new link (first time this
        exact (node_or_edge_id, source_document.id) pair was seen);
        False if it was already linked (a no-op repeat call).
    """
    existing = _EVIDENCE_STORE.setdefault(node_or_edge_id, [])
    if any(doc.id == source_document.id for doc in existing):
        return False
    existing.append(source_document)
    return True


def get_evidence_trail(entity_id: str) -> list[SourceDocument]:
    """Retrieve every source document linked to a flagged entity or
    pattern via link_evidence. Returns an empty list (not an error) if
    nothing has been linked yet — powers the dashboard's EvidencePanel
    component via api/routes/evidence.py.
    """
    return list(_EVIDENCE_STORE.get(entity_id, []))
