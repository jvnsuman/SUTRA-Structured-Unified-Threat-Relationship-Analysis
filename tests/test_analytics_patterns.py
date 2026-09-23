"""Anomaly detectors and influencer ranking on shapes real data has."""

from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import networkx as nx

from graph.analytics import detect_anomalies
from graph.build import highlight_influencer, rank_influencers


def _cdr(calls):
    return NS(doc_id="cdr", structured={"calls": calls})


def _call(a, b, ts):
    return {"caller": a, "callee": b, "timestamp": ts.isoformat(), "duration_seconds": 30}


def test_burst_is_found_inside_a_long_cdr():
    t0 = datetime(2026, 3, 1, 10, 0)
    calls = [_call("A", "B", t0 + timedelta(days=d, minutes=m)) for d in (0, 30, 60) for m in (0, 3, 6, 9)]
    calls += [_call("A", "C", t0 + timedelta(days=d)) for d in range(0, 90, 10)]      # steady, not a burst
    found = [f for f in detect_anomalies(nx.MultiDiGraph(), [_cdr(calls)]) if f["type"] == "communication_burst"]
    assert len(found) == 1 and found[0]["burst_count"] == 3 and found[0]["caller"] == "A" and found[0]["callee"] == "B"


def test_no_burst_for_evenly_spread_calls():
    t0 = datetime(2026, 3, 1)
    calls = [_call("A", "B", t0 + timedelta(hours=6 * i)) for i in range(20)]
    assert not [f for f in detect_anomalies(nx.MultiDiGraph(), [_cdr(calls)]) if f["type"] == "communication_burst"]


def test_unreadable_timestamps_do_not_crash_detection():
    calls = [{"caller": "A", "callee": "B", "timestamp": "not-a-date"}] * 5
    assert detect_anomalies(nx.MultiDiGraph(), [_cdr(calls)]) == []


def _fin(doc, receiver, amounts):
    return NS(doc_id=doc, structured={"sender_account": "S1", "receiver_account": receiver,
                                      "transactions": [{"amount": a} for a in amounts]})


def test_structuring_is_detected_across_records_by_sender():
    docs = [_fin("f1", "R1", [45_000, 46_000]), _fin("f2", "R2", [44_000, 49_000])]   # 2 per record: invisible per document
    found = [f for f in detect_anomalies(nx.MultiDiGraph(), docs) if f["type"] == "financial_structuring"]
    assert len(found) == 1 and found[0]["matching_transaction_count"] == 4 and "S1" in found[0]["detail"]


def test_one_big_or_one_small_payment_is_not_structuring():
    docs = [_fin("f1", "R1", [200_000, 5_000, 45_000])]
    assert not [f for f in detect_anomalies(nx.MultiDiGraph(), docs) if f["type"] == "financial_structuring"]


def _star():
    g = nx.MultiDiGraph()
    g.add_node("boss", entity_type="PERSON", canonical_text="Boss", source_doc_ids=["d1", "d2"])
    for i in range(4):
        g.add_node(f"r{i}", entity_type="PERSON", canonical_text=f"R{i}", source_doc_ids=["d1"])
        g.add_node(f"p{i}", entity_type="PHONE", canonical_text=f"9{i}", source_doc_ids=["d1"])
        g.add_edge(f"r{i}", f"p{i}", relation_type="calls")
        g.add_edge(f"p{i}", "boss", relation_type="calls")     # everyone reaches the boss THROUGH phones
    return g


def test_influencers_are_people_even_when_phones_sit_on_every_path():
    ranked = rank_influencers(_star(), top_n=3)
    assert ranked[0]["node_id"] == "boss" and all(r["node_id"][0] in "br" for r in ranked)
    assert "betweenness" in ranked[0]["reason"] and ranked[0]["rank"] == 1


def test_highlight_influencer_still_works_and_handles_empty_graph():
    assert highlight_influencer(_star())["node_id"] == "boss"
    assert highlight_influencer(nx.MultiDiGraph()) is None
