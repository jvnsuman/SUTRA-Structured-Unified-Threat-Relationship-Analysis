"""
graph/build.py

Stage 4: graph construction. Consumes nlp.resolution.ResolvedEntity
(canonical, deduplicated entities) and
nlp.relation_classification.ClassifiedRelation (relationships), and
assembles them into one queryable graph. Everything downstream
(centrality/influencer detection, the dashboard) reads from the graph
this module produces, not from either upstream module's raw output.

ID-mapping: ClassifiedRelation.entity_a_id/entity_b_id are raw
nlp.extraction.ExtractedEntity.id values — per-MENTION IDs, one per
occurrence in one document — NOT ResolvedEntity.id values. Building
edges straight from mention IDs would produce one graph node per
mention instead of per real-world entity (e.g. "Raju Kumar" and
"Raju S." would sit as two disconnected nodes even where resolution
correctly merged them). Always translate through
nlp.resolution.build_mention_to_resolved_map before adding an edge.

Library choice: NetworkX MultiDiGraph. A plain nx.Graph silently
overwrites a second edge added between the same two nodes (a "calls"
edge then a "transacts-with" edge between the same pair leaves only
the second). This schema allows two entities to have more than one
relationship type between them, so a MultiDiGraph is required to keep
every distinct edge. Direction matters too (A owns B doesn't imply B
owns A), so an undirected Graph would misrepresent the schema.

Edge merging: multiple documents classifying the SAME pair as the SAME
relation type are merged into one edge with a list of supporting
evidence (see _merge_or_add_edge), rather than left as separate
parallel edges — two independent documents confirming "calls" is
stronger evidence than one, and should read that way, not as two
unrelated low-context lines. Different relation TYPES between the same
pair still get separate parallel edges (that's what MultiDiGraph is
for).
"""

from __future__ import annotations

from typing import Optional

try:
    import networkx as nx
except ImportError as exc:  # pragma: no cover - environment guard
    raise ImportError(
        "networkx is required for graph/build.py. Install with:\n"
        "    pip install networkx --break-system-packages\n"
        "(see requirements.txt — networkx>=3.3,<4.0. Neo4j is deliberately "
        "excluded — see requirements.txt's license-conflict note.)"
    ) from exc

from nlp.resolution import ResolvedEntity, build_mention_to_resolved_map
from nlp.relation_classification import ClassifiedRelation, RelationType


def build_graph(resolved_entities: list,
                 relations: list) -> "nx.MultiDiGraph":
    """Construct a NetworkX MultiDiGraph from resolved entities and
    classified relations. Node keys are ResolvedEntity.id. Nodes for
    every resolved entity are always added, even with zero relations —
    an isolated entity is still a real finding worth surfacing.
    """
    graph = nx.MultiDiGraph()

    for entity in resolved_entities:
        graph.add_node(
            entity.id,
            entity_type=entity.entity_type.value,
            canonical_text=entity.canonical_text,
            mention_ids=list(entity.mention_ids),
            source_doc_ids=sorted(entity.source_doc_ids),
            is_low_confidence_cross_merge=entity.is_low_confidence_cross_merge,
            needs_review=entity.needs_review,
            merge_confidence=entity.merge_confidence,
            merge_reasons=list(entity.merge_reasons),
            aliases=list(entity.aliases),
            members=list(entity.members),
        )

    mention_to_resolved = build_mention_to_resolved_map(resolved_entities)

    for relation in relations:
        add_relation_edge(graph, relation, mention_to_resolved)

    return graph


def _find_existing_edge_key(graph: "nx.MultiDiGraph", source: str, target: str,
                             relation_type: str) -> Optional[int]:
    """Return the key of an existing source->target edge with this
    relation_type, or None if no such edge exists yet.
    """
    if not graph.has_edge(source, target):
        return None
    for key, data in graph[source][target].items():
        if data.get("relation_type") == relation_type:
            return key
    return None


def _merge_or_add_edge(graph: "nx.MultiDiGraph", source: str, target: str,
                        relation: ClassifiedRelation) -> int:
    """Add a new edge, or merge into an existing same-type edge between
    the same pair. Every edge (new or merged) carries an "evidence"
    list of {confidence, source_doc_id, source_text} — one entry per
    document that supports it — plus a top-level "confidence" equal to
    the max across all evidence, so existing readers of "confidence"
    (compute_centrality, dashboards) keep working unchanged.

    Also mirrors the first evidence entry's source_doc_id/source_text
    directly onto the edge itself (not just inside "evidence"), so
    get_evidence_trail can read them without reaching into the
    evidence list — kept in sync on every merge, not just at creation.
    """
    relation_type = relation.relation_type.value
    new_evidence = {
        "confidence": relation.confidence,
        "source_doc_id": relation.source_doc_id,
        "source_text": relation.source_text,
    }

    existing_key = _find_existing_edge_key(graph, source, target, relation_type)
    if existing_key is not None:
        edge_data = graph[source][target][existing_key]
        edge_data["evidence"].append(new_evidence)
        edge_data["confidence"] = max(e["confidence"] for e in edge_data["evidence"])
        # Keep top-level source_doc_id/source_text in sync with the
        # first evidence entry after every merge.
        edge_data["source_doc_id"] = edge_data["evidence"][0]["source_doc_id"]
        edge_data["source_text"] = edge_data["evidence"][0]["source_text"]
        return existing_key

    return graph.add_edge(
        source, target,
        relation_type=relation_type,
        confidence=relation.confidence,
        source_doc_id=relation.source_doc_id,
        source_text=relation.source_text,
        evidence=[new_evidence],
    )


def add_relation_edge(graph: "nx.MultiDiGraph", relation: ClassifiedRelation,
                       mention_to_resolved: dict) -> Optional[str]:
    """Add one ClassifiedRelation to graph as a directed edge between
    the RESOLVED entities its two mention IDs belong to, merging into
    an existing same-type edge if one already exists for this pair
    (see _merge_or_add_edge).

    Skips (returns None) in three expected/recoverable cases:
        1. relation.relation_type is UNRELATED.
        2. Either entity ID isn't in mention_to_resolved (the relation
           references a mention resolution.py never saw).
        3. The edge would be a self-loop (both mention IDs resolved to
           the same entity).

    Returns:
        The edge key if an edge was added or merged into, or None if skipped.
    """
    if relation.relation_type == RelationType.UNRELATED:
        return None

    resolved_a = mention_to_resolved.get(relation.entity_a_id)
    resolved_b = mention_to_resolved.get(relation.entity_b_id)

    if resolved_a is None or resolved_b is None:
        return None

    if resolved_a == resolved_b:
        return None

    return _merge_or_add_edge(graph, resolved_a, resolved_b, relation)


def get_evidence_trail(graph: "nx.MultiDiGraph", node_id: str) -> dict:
    """Retrieve everything needed to explain why a node/its edges
    exist, tracing back to source documents — every edge's full
    evidence list (all supporting documents/confidences), not just one.

    Raises:
        KeyError: if node_id is not present in graph.
    """
    if node_id not in graph:
        raise KeyError(f"Node {node_id!r} not found in graph")

    outgoing_edges = [
        {
            "neighbor_id": target,
            "relation_type": data["relation_type"],
            "confidence": data["confidence"],
            "source_doc_id": data.get("source_doc_id"),
            "source_text": data.get("source_text"),
            "evidence": data["evidence"],
        }
        for _, target, data in graph.out_edges(node_id, data=True)
    ]
    incoming_edges = [
        {
            "neighbor_id": source,
            "relation_type": data["relation_type"],
            "confidence": data["confidence"],
            "source_doc_id": data.get("source_doc_id"),
            "source_text": data.get("source_text"),
            "evidence": data["evidence"],
        }
        for source, _, data in graph.in_edges(node_id, data=True)
    ]

    return {
        "node_attributes": dict(graph.nodes[node_id]),
        "outgoing_edges": outgoing_edges,
        "incoming_edges": incoming_edges,
    }


def _as_simple_digraph(graph: "nx.MultiDiGraph") -> "nx.DiGraph":
    """Collapse to a plain DiGraph for centrality algorithms that
    don't operate on multigraphs. Attributes aren't preserved — used
    internally for structural centrality math only.
    """
    simple = nx.DiGraph()
    simple.add_nodes_from(graph.nodes())
    simple.add_edges_from(graph.edges())
    return simple


def compute_centrality(graph: "nx.MultiDiGraph", method: str = "betweenness") -> dict:
    """Run a centrality algorithm over the graph.

    Raises:
        ValueError: if method is not one of the supported options.
    """
    simple = _as_simple_digraph(graph)
    if method == "betweenness":
        return nx.betweenness_centrality(simple)
    if method == "pagerank":
        return nx.pagerank(simple)
    raise ValueError(f"Unknown centrality method: {method!r}. Use 'betweenness' or 'pagerank'.")


def rank_influencers(graph: "nx.MultiDiGraph", top_n: int = 5, method: str = "betweenness",
                     entity_types: tuple = ("PERSON",)) -> list:
    """Rank the graph's key INDIVIDUALS by centrality, each with a stated,
    checkable reason.

    Two deliberate choices (both found by running the planted trafficking
    ring through this, see tests/test_trafficking_end_to_end.py):

    * Only entities of `entity_types` are ranked (people by default).
      The problem asks for influential *individuals*; ranking all nodes
      put a handler's phone number, which sits on every path to him, at
      the top instead of the handler.
    * Centrality is computed on the UNDIRECTED graph. Relations such as
      "calls" or "associated-with" are stored directionally, but two
      recruiters sharing one handler are connected through him either
      way; directed betweenness misses paths that run "against" an edge.

    Returns [] for an empty graph. Falls back to all nodes if the graph
    contains none of the requested types.
    """
    import networkx as nx

    if graph.number_of_nodes() == 0:
        return []
    undirected = nx.Graph(graph.to_undirected(as_view=False))
    if method == "betweenness":
        scores = nx.betweenness_centrality(undirected)
    elif method == "pagerank":
        scores = nx.pagerank(undirected)
    else:
        raise ValueError(f"Unknown centrality method: {method!r}. Use 'betweenness' or 'pagerank'.")

    wanted = {t.upper() for t in entity_types}
    candidates = [n for n, a in graph.nodes(data=True) if str(a.get("entity_type", "")).upper() in wanted]
    scope = "people" if candidates else "entities"
    if not candidates:
        candidates = list(graph.nodes())

    ranked = sorted(candidates, key=lambda n: (-scores.get(n, 0.0), str(n)))[:top_n]
    out = []
    for rank, node_id in enumerate(ranked, start=1):
        attrs = graph.nodes[node_id]
        neighbours = set(undirected.neighbors(node_id))
        types: dict = {}
        for n in neighbours:
            t = str(graph.nodes[n].get("entity_type", "?")).lower()
            types[t] = types.get(t, 0) + 1
        mix = ", ".join(f"{c} {t}" for t, c in sorted(types.items(), key=lambda kv: -kv[1]))
        docs = len(attrs.get("source_doc_ids", []) or [])
        out.append({
            "node_id": node_id, "rank": rank, "score": scores.get(node_id, 0.0), "method": method,
            "degree": len(neighbours),
            "reason": (f"Ranked #{rank} of {len(candidates)} {scope} by {method} centrality "
                       f"({scores.get(node_id, 0.0):.3f}): linked to {len(neighbours)} entities ({mix}) "
                       f"across {docs} source document(s)."),
        })
    return out


def highlight_influencer(graph: "nx.MultiDiGraph", method: str = "betweenness") -> Optional[dict]:
    """The single top-ranked individual (see rank_influencers) with a
    stated reason, or None if the graph has no nodes."""
    ranked = rank_influencers(graph, top_n=1, method=method)
    return ranked[0] if ranked else None


def render_graph(graph: "nx.MultiDiGraph", output_path: str = "graph.png") -> str:
    """Render a static PNG for quick local sanity-checking. Not a
    dashboard substitute — the interactive experience lives in
    dashboard/'s GraphCanvas component.

    Raises:
        RuntimeError: if matplotlib is not installed.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise RuntimeError(
            "matplotlib is required for render_graph but is not installed. Run:\n"
            "    pip install matplotlib"
        )

    simple = _as_simple_digraph(graph)
    pos = nx.spring_layout(simple, seed=42)
    labels = {n: graph.nodes[n].get("canonical_text", n) for n in graph.nodes()}
    colors = [_ENTITY_TYPE_COLORS.get(graph.nodes[n].get("entity_type"), "#999999") for n in graph.nodes()]

    plt.figure(figsize=(12, 8))
    nx.draw(
        simple, pos, labels=labels, node_color=colors, with_labels=True,
        node_size=800, font_size=8, arrows=True,
    )
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    return output_path


_ENTITY_TYPE_COLORS = {
    "PERSON": "#4f8ef7",
    "LOCATION": "#f7b84f",
    "VEHICLE": "#8e6de6",
    "PHONE": "#4fd6c0",
    "ORGANIZATION": "#f76b6b",
    "ACCOUNT": "#7bd88f",
}


if __name__ == "__main__":
    # Smoke test chaining extraction -> resolution -> relation
    # classification -> graph construction. Requires transformers +
    # torch for the relation-classification step.
    from nlp.extraction import load_synthetic_fir, extract_entities
    from nlp.resolution import resolve_entities

    doc_a = load_synthetic_fir(
        "Complainant stated that Raju Kumar was seen near MG Road, "
        "contacted from phone number 987-654-3210.",
        doc_id="fir-001",
    )
    doc_b = load_synthetic_fir(
        "A call from 987-654-3210 was traced to the same MG Road area.",
        doc_id="cdr-002",
    )

    all_entities = []
    for doc in (doc_a, doc_b):
        all_entities.extend(extract_entities(doc.raw_text, doc.doc_id))

    resolved = resolve_entities(all_entities)
    print("--- Resolved entities ---")
    for r in resolved:
        print(f"  {r.entity_type.value:12} {r.canonical_text!r:20} "
              f"docs={sorted(r.source_doc_ids)}")

    try:
        from nlp.relation_classification import classify_all_relations
        relations = []
        for doc, doc_entities in (
            (doc_a, [e for e in all_entities if e.source_doc_id == doc_a.doc_id]),
            (doc_b, [e for e in all_entities if e.source_doc_id == doc_b.doc_id]),
        ):
            relations.extend(classify_all_relations(doc_entities, doc.raw_text))

        graph = build_graph(resolved, relations)
        print(f"\n--- Graph: {graph.number_of_nodes()} nodes, "
              f"{graph.number_of_edges()} edges ---")
        for node_id, attrs in graph.nodes(data=True):
            print(f"  [{attrs['entity_type']}] {attrs['canonical_text']!r} "
                  f"(docs={attrs['source_doc_ids']})")
        for u, v, data in graph.edges(data=True):
            doc_count = len(data["evidence"])
            print(f"  {u} --[{data['relation_type']}]--> {v} "
                  f"(confidence={data['confidence']:.2f}, "
                  f"confirmed across {doc_count} document{'s' if doc_count != 1 else ''})")

    except RuntimeError as e:
        print(f"\n(Skipping relation-classification/graph portion — {e})")
        print("Building graph with entities only, no edges:")
        graph = build_graph(resolved, [])
        print(f"  {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")