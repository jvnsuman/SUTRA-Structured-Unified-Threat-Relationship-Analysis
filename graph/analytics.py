"""
graph/analytics.py

Deeper graph analytics, built on top of graph/build.py's core
centrality (compute_centrality). These extend the same graph object —
no separate analytics pipeline.

Scope note on detect_anomalies: the graph object alone (nodes/edges
from resolved entities and classified relations) doesn't carry raw
transaction amounts or call timestamps — that data lives in each
source document's `structured` field (see
data/generate_synthetic.py's SyntheticDocument), never embedded into
graph attributes by graph.build.build_graph. So detect_anomalies takes
the graph AND the original source documents: the graph-structural
check (hub-and-spoke degree) uses the graph alone; the financial-
structuring and communication-burst checks scan the raw documents
directly, independent of the graph.

Scope note on compute_link_predictions: this predicts likely-but-
unrecorded RELATIONSHIPS between two already-known entities within an
already-open case's graph (a structural lead — "these two share
several neighbors but have no direct edge yet" — for an investigator
to manually verify), NOT a risk score or a prediction about any
individual's future behavior. It never runs against, or produces a
score for, an entity outside the case's own already-extracted entity
set. This is the same network-analysis-not-predictive-policing
boundary the project as a whole holds to (see README.md: "not
predictive policing... flags network positions").

Status: implemented, not yet tuned against any labelled/real dataset —
thresholds below are reasonable starting points, not validated cutoffs.
"""

from typing import Optional

import networkx as nx

from graph.build import compute_centrality


def compute_community_detection(graph: "nx.MultiDiGraph") -> dict:
    """Run Louvain community detection to surface clusters that may
    represent sub-networks (e.g. a trafficking recruitment cell) within
    a larger graph.

    Args:
        graph: a graph produced by graph.build.build_graph.

    Returns:
        dict mapping node_id -> community_id (an integer, stable only
        within this one call — re-running may assign different integers
        to the same community due to Louvain's randomized tie-breaking).
    """
    undirected = nx.Graph()
    undirected.add_nodes_from(graph.nodes())
    undirected.add_edges_from(graph.edges())

    communities = nx.algorithms.community.louvain_communities(undirected, seed=42)
    return {
        node_id: community_index
        for community_index, community in enumerate(communities)
        for node_id in community
    }


def compute_link_predictions(graph: "nx.MultiDiGraph", top_n: int = 10) -> list[dict]:
    """Predict likely-but-unrecorded relationships between entities
    that don't already have a direct edge, based on how many neighbors
    they already share — NOT a prediction about any individual's
    future behavior (see module docstring's predictive-policing
    guardrail). This surfaces network STRUCTURE the extraction/
    resolution pipeline hasn't (yet) recorded a direct document for —
    e.g. two people who both call the same three phone numbers but
    have no FIR/CDR directly connecting them to each other — as a
    lead for an investigator to manually verify, never as an
    automatic edge or a standing accusation.

    Uses NetworkX's Adamic-Adar index (nx.adamic_adar_index), scored
    over the graph collapsed to a plain undirected nx.Graph (link
    prediction on a directed multigraph isn't a standard operation;
    direction and multi-edge-type detail from the original graph
    don't change "do these two share neighbors", which is what this
    measures). Adamic-Adar down-weights common neighbors that
    themselves have very high degree (e.g. a shared "associated-with"
    hub entity connected to hundreds of others is weak evidence of a
    real link, vs. sharing a neighbor with only two or three
    connections total), which plain shared-neighbor counting would
    over-value.

    Args:
        graph: the case's built graph (graph.build.build_graph output).
        top_n: how many highest-scoring pairs to return.

    Returns:
        Up to top_n dicts, sorted by score descending, each:
            {"entity_a_id", "entity_b_id", "score", "shared_neighbor_ids"}
        Empty list if the graph has fewer than 2 nodes, or if no
        candidate pair shares any neighbor at all (score is 0 for
        every non-adjacent pair in that case, and 0-score pairs are
        excluded — a 0 score is "no shared-neighbor signal", not a
        weak prediction worth surfacing).
    """
    if graph.number_of_nodes() < 2:
        return []

    undirected = _as_simple_undirected_graph(graph)
    # IMPORTANT: nx.non_edges returns a generator, and passing it
    # directly into adamic_adar_index silently produces an EMPTY
    # result — verified empirically; adamic_adar_index's
    # @nx._dispatchable decorator appears to inspect/consume the
    # generator before the real computation runs. Materializing it to
    # a concrete list first is required.
    non_adjacent_pairs = list(nx.non_edges(undirected))

    scored_pairs = list(nx.adamic_adar_index(undirected, non_adjacent_pairs))
    scored_pairs = [(a, b, score) for a, b, score in scored_pairs if score > 0]
    scored_pairs.sort(key=lambda item: item[2], reverse=True)

    predictions = []
    for entity_a_id, entity_b_id, score in scored_pairs[:top_n]:
        shared_neighbor_ids = sorted(set(undirected.neighbors(entity_a_id)) & set(undirected.neighbors(entity_b_id)))
        predictions.append({
            "entity_a_id": entity_a_id,
            "entity_b_id": entity_b_id,
            "score": score,
            "shared_neighbor_ids": shared_neighbor_ids,
        })

    return predictions


def _as_simple_undirected_graph(graph: "nx.MultiDiGraph") -> "nx.Graph":
    """Collapse to a plain undirected nx.Graph — required by NetworkX's
    link-prediction functions (nx.adamic_adar_index and siblings),
    which don't accept a MultiDiGraph. Parallel to graph.build's own
    _as_simple_digraph (that one keeps direction, for centrality;
    this one drops it, since shared-neighbor overlap is inherently a
    symmetric question — "does A share a neighbor with B" doesn't
    depend on which direction either edge originally pointed).
    """
    simple = nx.Graph()
    simple.add_nodes_from(graph.nodes())
    simple.add_edges_from(graph.edges())
    return simple


def compute_full_centrality_suite(graph: "nx.MultiDiGraph") -> dict:
    """Compute betweenness, PageRank, and eigenvector centrality
    together, for callers that want the full picture rather than one
    method at a time via graph.build.compute_centrality.

    Args:
        graph: a graph produced by graph.build.build_graph.

    Returns:
        dict mapping node_id -> {"betweenness": float, "pagerank": float,
        "eigenvector": float}. Eigenvector centrality is 0.0 for every
        node if the algorithm fails to converge (e.g. on a
        disconnected or very small graph) — this is a documented
        NetworkX behavior, not a bug, so it's caught and defaulted
        rather than raising.
    """
    betweenness = compute_centrality(graph, method="betweenness")
    pagerank = compute_centrality(graph, method="pagerank")

    simple = nx.DiGraph()
    simple.add_nodes_from(graph.nodes())
    simple.add_edges_from(graph.edges())
    try:
        eigenvector = nx.eigenvector_centrality(simple, max_iter=1000)
    except nx.PowerIterationFailedConvergence:
        eigenvector = {node_id: 0.0 for node_id in graph.nodes()}

    return {
        node_id: {
            "betweenness": betweenness.get(node_id, 0.0),
            "pagerank": pagerank.get(node_id, 0.0),
            "eigenvector": eigenvector.get(node_id, 0.0),
        }
        for node_id in graph.nodes()
    }


# Hub-and-spoke detection: a node whose degree exceeds the graph's
# average by this many standard deviations is flagged as a potential
# recruitment/coordination hub. Not tuned against any labelled data.
_HUB_DEGREE_STDEV_THRESHOLD = 2.0
_MIN_NODES_FOR_HUB_DETECTION = 4

# Financial structuring: transactions repeatedly falling within this
# fraction of a threshold are flagged (mirrors
# data/generate_synthetic.py's generate_synthetic_financial_record
# structuring_pattern shape, detected independently rather than
# trusting that flag).
_STRUCTURING_THRESHOLD = 50_000
_STRUCTURING_LOWER_BOUND_FRACTION = 0.8
_STRUCTURING_MIN_COUNT = 3

# Communication burst: this many-or-more calls between the same pair
# within this window are flagged (mirrors generate_synthetic_cdr's
# burst=True shape).
_BURST_WINDOW_MINUTES = 15
_BURST_MIN_CALLS = 3


def _detect_hub_and_spoke(graph: "nx.MultiDiGraph") -> list[dict]:
    """Graph-structural check: nodes with unusually high degree
    relative to the rest of the graph.
    """
    if graph.number_of_nodes() < _MIN_NODES_FOR_HUB_DETECTION:
        return []

    degrees = dict(graph.degree())
    values = list(degrees.values())
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    stdev = variance ** 0.5
    if stdev == 0:
        return []

    threshold = mean + _HUB_DEGREE_STDEV_THRESHOLD * stdev
    return [
        {
            "type": "hub_and_spoke",
            "node_id": node_id,
            "degree": degree,
            "graph_mean_degree": round(mean, 2),
            "label": graph.nodes[node_id].get("canonical_text", str(node_id)),
            "detail": (f"{graph.nodes[node_id].get('canonical_text', node_id)} "
                       f"({str(graph.nodes[node_id].get('entity_type', 'entity')).lower()}) is linked to {degree} entities, "
                       f"{_HUB_DEGREE_STDEV_THRESHOLD}+ standard deviations above the graph average ({mean:.2f})."),
        }
        for node_id, degree in degrees.items()
        if degree > threshold
    ]


def _detect_financial_structuring(documents: list) -> list[dict]:
    """Scan raw financial-record documents for several transactions
    clustered just under _STRUCTURING_THRESHOLD.
    """
    findings = []
    for doc in documents:
        transactions = getattr(doc, "structured", {}).get("transactions")
        if not transactions:
            continue

        lower_bound = _STRUCTURING_THRESHOLD * _STRUCTURING_LOWER_BOUND_FRACTION
        suspicious = [
            t for t in transactions
            if lower_bound <= t.get("amount", 0) < _STRUCTURING_THRESHOLD
        ]
        if len(suspicious) >= _STRUCTURING_MIN_COUNT:
            findings.append({
                "type": "financial_structuring",
                "doc_id": getattr(doc, "doc_id", None),
                "matching_transaction_count": len(suspicious),
                "detail": (
                    f"{len(suspicious)} transactions between "
                    f"{lower_bound:.0f} and {_STRUCTURING_THRESHOLD} "
                    f"(just under the reporting threshold)."
                ),
            })
    return findings


def _parse_ts(value):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _detect_communication_bursts(documents: list) -> list[dict]:
    """Scan CDR documents for BURSTS: >= _BURST_MIN_CALLS calls between the
    same caller/callee pair inside a sliding _BURST_WINDOW_MINUTES window.

    The earlier version flagged a document only if ALL of its calls fell
    inside one window -- fine for a synthetic single-burst record, but a
    real CDR spans weeks or months, so it would essentially never fire.
    Now every pair is scanned with a sliding window, the number of separate
    bursts is counted (a pair that bursts repeatedly, e.g. before each
    transport, is the pattern that matters), and unreadable timestamps are
    skipped instead of crashing the alerts endpoint.
    """
    from datetime import timedelta

    window = timedelta(minutes=_BURST_WINDOW_MINUTES)
    findings = []
    for doc in documents:
        calls = getattr(doc, "structured", {}).get("calls")
        if not calls or len(calls) < _BURST_MIN_CALLS:
            continue
        by_pair: dict = {}
        for c in calls:
            ts = _parse_ts(c.get("timestamp"))
            if ts is None:
                continue
            by_pair.setdefault((str(c.get("caller")), str(c.get("callee"))), []).append(ts)

        for (caller, callee), stamps in by_pair.items():
            stamps.sort()
            bursts, longest, i = 0, 0, 0
            in_burst_until = -1
            for j in range(len(stamps)):
                while stamps[j] - stamps[i] > window:
                    i += 1
                size = j - i + 1
                if size >= _BURST_MIN_CALLS and j > in_burst_until:
                    bursts += 1
                    longest = max(longest, size)
                    in_burst_until = j
                    # skip past this burst so one burst is counted once
                    while j + 1 < len(stamps) and stamps[j + 1] - stamps[i] <= window:
                        j += 1
                        in_burst_until = j
                        longest = max(longest, j - i + 1)
            if bursts:
                findings.append({
                    "type": "communication_burst",
                    "doc_id": getattr(doc, "doc_id", None),
                    "caller": caller, "callee": callee,
                    "burst_count": bursts, "call_count": len(stamps), "largest_burst": longest,
                    "detail": (f"{caller} -> {callee}: {bursts} burst(s) of {_BURST_MIN_CALLS}+ calls within "
                               f"{_BURST_WINDOW_MINUTES} minutes (largest {longest} calls; {len(stamps)} calls in total)."),
                })
    return findings


def _detect_structuring_by_sender(documents: list) -> list[dict]:
    """Aggregate sub-threshold payments PER SENDER ACCOUNT across all
    financial documents. A handler paying four recruiters 45,000 each is
    invisible per-document (each record may hold only 2 payments) but
    obvious once summed by sender -- the classic layering pattern.
    """
    lower_bound = _STRUCTURING_THRESHOLD * _STRUCTURING_LOWER_BOUND_FRACTION
    by_sender: dict = {}
    for doc in documents:
        s = getattr(doc, "structured", {}) or {}
        sender, receiver = s.get("sender_account"), s.get("receiver_account")
        for t in s.get("transactions") or []:
            amt = t.get("amount", 0) if isinstance(t, dict) else 0
            if sender and lower_bound <= amt < _STRUCTURING_THRESHOLD:
                rec = by_sender.setdefault(str(sender), {"n": 0, "total": 0.0, "receivers": set(), "docs": set()})
                rec["n"] += 1
                rec["total"] += amt
                rec["receivers"].add(str(receiver))
                rec["docs"].add(getattr(doc, "doc_id", None))
    findings = []
    for sender, rec in by_sender.items():
        if rec["n"] >= _STRUCTURING_MIN_COUNT and len(rec["receivers"]) >= 2:
            findings.append({
                "type": "financial_structuring",
                "node_id": f"sender-{sender}",
                "matching_transaction_count": rec["n"],
                "detail": (f"Account {sender} made {rec['n']} payments just under {_STRUCTURING_THRESHOLD:,} "
                           f"(total {rec['total']:,.0f}) to {len(rec['receivers'])} different accounts across "
                           f"{len(rec['docs'])} records."),
            })
    return findings


def detect_anomalies(graph: "nx.MultiDiGraph", documents: Optional[list] = None) -> list[dict]:
    """Flag suspicious patterns: hub-and-spoke structural outliers
    (from the graph), plus financial-flow structuring and
    communication-burst detection (from the raw source documents, if
    provided — see module docstring for why these can't be detected
    from the graph object alone).

    Args:
        graph: a graph produced by graph.build.build_graph.
        documents: optional list of source documents (e.g.
            data/generate_synthetic.py's SyntheticDocument, or anything
            duck-typed the same way: .doc_id and .structured). If
            omitted, only the graph-structural check runs.

    Returns:
        A list of finding dicts, each with at least a "type" and
        "detail" key. Empty list if nothing was flagged.
    """
    findings = _detect_hub_and_spoke(graph)
    if documents:
        findings.extend(_detect_financial_structuring(documents))
        findings.extend(_detect_structuring_by_sender(documents))
        findings.extend(_detect_communication_bursts(documents))
    return findings
