"""
tests/test_graph_pipeline.py

Tests for graph/build.py and graph/analytics.py, exercised against
real entities/relations from the real extraction/resolution pipeline
— not hand-built fixtures, so these also catch integration breaks
between the NLP and graph layers.
"""

import uuid

import pytest

from data.generate_synthetic import generate_synthetic_cdr, generate_synthetic_financial_record
from graph.analytics import compute_community_detection, compute_full_centrality_suite, compute_link_predictions, detect_anomalies
from graph.build import build_graph, compute_centrality, get_evidence_trail, highlight_influencer
from graph.explainability import get_evidence_trail as get_linked_evidence
from graph.explainability import link_evidence
from nlp.extraction import extract_entities
from nlp.relation_classification import ClassifiedRelation, RelationType
from nlp.resolution import build_mention_to_resolved_map, resolve_entities
from schema.entities import SourceDocument


def _build_sample_graph():
    text = "Raju Kumar called from 9876543210 near MG Road."
    entities = extract_entities(text, "doc-1")
    resolved = resolve_entities(entities)

    person = next(e for e in entities if e.entity_type.value == "PERSON")
    phone = next(e for e in entities if e.entity_type.value == "PHONE")
    relation = ClassifiedRelation(
        id=str(uuid.uuid4()), entity_a_id=person.id, entity_b_id=phone.id,
        relation_type=RelationType.CALLS, confidence=0.9,
        source_doc_id="doc-1", source_text=text,
    )
    graph = build_graph(resolved, [relation])
    mapping = build_mention_to_resolved_map(resolved)
    return graph, mapping, person, phone


def test_build_graph_creates_nodes_and_edges():
    graph, *_ = _build_sample_graph()
    assert graph.number_of_nodes() >= 2
    assert graph.number_of_edges() == 1


def test_get_evidence_trail_traces_back_to_source():
    graph, mapping, person, _ = _build_sample_graph()
    node_id = mapping[person.id]
    trail = get_evidence_trail(graph, node_id)
    assert trail["outgoing_edges"][0]["source_doc_id"] == "doc-1"


def test_compute_centrality_rejects_unknown_method():
    graph, *_ = _build_sample_graph()
    with pytest.raises(ValueError):
        compute_centrality(graph, method="bogus")


def test_highlight_influencer_returns_none_for_empty_graph():
    import networkx as nx
    assert highlight_influencer(nx.MultiDiGraph()) is None


def test_highlight_influencer_returns_a_node_for_nonempty_graph():
    graph, *_ = _build_sample_graph()
    result = highlight_influencer(graph)
    assert result is not None
    assert result["node_id"] in graph.nodes()


def test_compute_full_centrality_suite_covers_every_node():
    graph, *_ = _build_sample_graph()
    suite = compute_full_centrality_suite(graph)
    assert set(suite.keys()) == set(graph.nodes())
    for scores in suite.values():
        assert set(scores.keys()) == {"betweenness", "pagerank", "eigenvector"}


def test_compute_community_detection_covers_every_node():
    graph, *_ = _build_sample_graph()
    communities = compute_community_detection(graph)
    assert set(communities.keys()) == set(graph.nodes())


def test_detect_anomalies_flags_planted_communication_burst():
    burst_cdr = generate_synthetic_cdr(burst=True, num_calls=5)
    import networkx as nx
    findings = detect_anomalies(nx.MultiDiGraph(), [burst_cdr])
    assert any(f["type"] == "communication_burst" for f in findings)


def test_detect_anomalies_does_not_flag_normal_cdr():
    normal_cdr = generate_synthetic_cdr(burst=False, num_calls=5)
    import networkx as nx
    findings = detect_anomalies(nx.MultiDiGraph(), [normal_cdr])
    assert not any(f["type"] == "communication_burst" for f in findings)


def test_detect_anomalies_flags_planted_financial_structuring():
    structuring_fin = generate_synthetic_financial_record(structuring_pattern=True, num_transactions=4)
    import networkx as nx
    findings = detect_anomalies(nx.MultiDiGraph(), [structuring_fin])
    assert any(f["type"] == "financial_structuring" for f in findings)


def test_link_evidence_and_get_evidence_trail_round_trip():
    doc = SourceDocument(id="D1", document_type="fir", raw_text="On 12th March...")
    link_evidence("entity-123", doc)
    trail = get_linked_evidence("entity-123")
    assert len(trail) == 1
    assert trail[0].id == "D1"


def test_link_evidence_is_idempotent():
    doc = SourceDocument(id="D2", document_type="fir", raw_text="text")
    link_evidence("entity-456", doc)
    link_evidence("entity-456", doc)
    assert len(get_linked_evidence("entity-456")) == 1


def test_link_evidence_return_value_distinguishes_new_from_repeat():
    """link_evidence returns True only the first time a given
    (entity_id, document.id) pair is linked — api/routes/query.py
    relies on this to decide whether to append a ledger entry
    (ledger/chain.py), so a repeat query doesn't re-log the same
    evidence-linking event on every page load.
    """
    doc = SourceDocument(id="D-return-value-check", document_type="fir", raw_text="text")
    first_call = link_evidence("entity-return-value-check", doc)
    second_call = link_evidence("entity-return-value-check", doc)
    assert first_call is True
    assert second_call is False


def test_get_evidence_trail_empty_for_unknown_entity():
    assert get_linked_evidence("never-linked-entity") == []


def test_compute_link_predictions_ranks_shared_neighbor_pairs_highest():
    """Regression test for a real bug: passing nx.non_edges(...) as a
    raw generator into nx.adamic_adar_index silently returns an empty
    result (verified empirically — its @nx._dispatchable decorator
    appears to consume the generator before computing). This test
    fails loudly if that materialize-to-a-list fix is ever reverted.
    """
    import networkx as nx

    g = nx.MultiDiGraph()
    for node in ["A", "B", "C", "D", "E"]:
        g.add_node(node)
    g.add_edge("A", "C", relation_type="calls")
    g.add_edge("B", "C", relation_type="calls")
    g.add_edge("A", "D", relation_type="transacts-with")
    g.add_edge("B", "D", relation_type="transacts-with")
    g.add_edge("E", "A", relation_type="calls")

    predictions = compute_link_predictions(g, top_n=5)

    assert len(predictions) > 0, "compute_link_predictions returned nothing — check the nx.non_edges generator fix"
    top = predictions[0]
    assert {top["entity_a_id"], top["entity_b_id"]} == {"A", "B"}
    assert set(top["shared_neighbor_ids"]) == {"C", "D"}
    assert top["score"] > predictions[1]["score"]


def test_compute_link_predictions_excludes_already_connected_pairs():
    import networkx as nx

    g = nx.MultiDiGraph()
    g.add_edge("A", "B", relation_type="calls")

    predictions = compute_link_predictions(g, top_n=10)

    pair_ids = [{p["entity_a_id"], p["entity_b_id"]} for p in predictions]
    assert {"A", "B"} not in pair_ids


def test_compute_link_predictions_empty_graph_returns_empty_list():
    import networkx as nx

    assert compute_link_predictions(nx.MultiDiGraph()) == []


def test_compute_link_predictions_single_node_returns_empty_list():
    import networkx as nx

    g = nx.MultiDiGraph()
    g.add_node("A")
    assert compute_link_predictions(g) == []
