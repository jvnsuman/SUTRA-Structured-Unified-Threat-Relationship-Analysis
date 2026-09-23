"""
tests/test_trafficking_end_to_end.py

The whole system, through the real API, on a planted trafficking ring
(data/trafficking_scenario.py): ingest -> extraction -> resolution ->
graph -> analytics -> evidence trail -> review workflow.

What must be *discovered* (not merely displayed):
  * the handler, written down in many different ways across documents,
    is one node, and is the top influencer;
  * the structuring and communication-burst patterns raise alerts;
  * every node's evidence trail leads back to source documents, and is
    persisted;
  * re-querying a case does not write new ledger entries;
  * an investigator can split a wrong merge and it sticks.
"""

import pytest

from data.trafficking_scenario import generate_trafficking_ring
from tests.conftest import auth_headers

_TYPE = {"FIR": "fir", "CDR": "cdr", "FINANCIAL_RECORD": "financial"}


def _doc_type(d):
    return _TYPE[d.doc_type.name] if d.doc_type.name in _TYPE else "fir"


@pytest.fixture
def ring_case(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")
    case_id = client.post("/cases/", json={"title": "Border-route ring", "description": "planted"},
                          headers=headers).json()["id"]
    ring = generate_trafficking_ring(seed=26189)
    for d in ring.documents:
        body = {"id": d.doc_id, "document_type": _doc_type(d), "case_id": case_id,
                "raw_text": d.text or f"Structured {d.doc_type.name} record {d.doc_id}",
                "structured": d.structured or {}}
        r = client.post("/ingest/", json=body, headers=headers)
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "extracted"
    return ring, case_id, headers


def test_handler_is_one_node_and_top_influencer(client, ring_case):
    ring, case_id, headers = ring_case
    body = client.get(f"/query/{case_id}", headers=headers).json()
    labels = {n["id"]: n for n in body["nodes"]}
    top = labels[body["influencer"]["node_id"]]
    handler_names = {x.lower() for x in ring.plan["handler_names"]}
    forms = {top["label"].lower()} | {a.lower() for a in top["aliases"]}
    # the top influencer IS the handler, under whichever surface form is canonical
    assert any(h in f or f in h for h in handler_names for f in forms), (top["label"], top["aliases"])
    assert top["entity_type"] == "person"
    assert len(top["members"]) >= 5   # merged from many documents/surface forms


def test_handler_phones_are_all_linked_to_the_handler(client, ring_case):
    ring, case_id, headers = ring_case
    body = client.get(f"/query/{case_id}", headers=headers).json()
    nodes = {n["id"]: n for n in body["nodes"]}
    top_id = body["influencer"]["node_id"]
    neighbours = {e["target"] if e["source"] == top_id else e["source"]
                  for e in body["edges"] if top_id in (e["source"], e["target"])}
    neighbour_labels = {nodes[n]["label"] for n in neighbours}
    linked = [p for p in ring.plan["handler_phones"] if p in neighbour_labels]
    assert len(linked) >= 2, (linked, neighbour_labels)


def test_planted_patterns_raise_alerts(client, ring_case):
    ring, case_id, headers = ring_case
    alerts = client.get(f"/alerts/{case_id}", headers=headers).json()["alerts"]
    titles = " ".join(a["title"] for a in alerts)
    assert "Suspicious Transaction" in titles          # structuring under Rs 50,000
    assert "Communication Burst" in titles             # recruiter -> handler bursts


def test_evidence_trail_is_persisted_and_case_scoped(client, ring_case, db_session):
    ring, case_id, headers = ring_case
    body = client.get(f"/query/{case_id}", headers=headers).json()
    top_id = body["influencer"]["node_id"]

    r = client.get(f"/evidence/{top_id}", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["evidence"]) >= 3 and r.json()["case_id"] == case_id

    # a user with no access to the case cannot read its evidence
    from db import repository as repo
    from schema.user import Role, User, hash_password
    repo.create_user(db_session, User(id="u77", name="Outsider", badge_id="B077", agency_id="AG2",
                                      role=Role.INVESTIGATOR, password_hash=hash_password("pw77")))
    other = auth_headers(client, "B077", "pw77")
    assert client.get(f"/evidence/{top_id}", headers=other).status_code == 403


def test_requerying_does_not_grow_the_ledger(client, ring_case):
    ring, case_id, headers = ring_case
    client.get(f"/query/{case_id}", headers=headers)
    n1 = len(client.get("/ledger/entries", headers=headers).json()["entries"])
    for _ in range(3):
        client.get(f"/query/{case_id}", headers=headers)
    n2 = len(client.get("/ledger/entries", headers=headers).json()["entries"])
    assert n1 == n2
    assert client.get("/ledger/verify", headers=headers).json()["valid"] is True


def test_investigator_can_split_a_merge_and_it_sticks(client, ring_case):
    ring, case_id, headers = ring_case
    body = client.get(f"/query/{case_id}", headers=headers).json()
    top = next(n for n in body["nodes"] if n["id"] == body["influencer"]["node_id"])
    a, b = top["members"][0]["mention_id"], top["members"][-1]["mention_id"]
    assert a != b

    r = client.post(f"/query/{case_id}/resolution/overrides", headers=headers,
                    json={"mention_a_id": a, "mention_b_id": b, "action": "never_merge", "note": "different people"})
    assert r.status_code == 201

    body2 = client.get(f"/query/{case_id}", headers=headers).json()
    for node in body2["nodes"]:
        ids = {m["mention_id"] for m in node["members"]}
        assert not ({a, b} <= ids), "split mentions must not share a node"


def test_analyst_can_read_but_not_override(client, ring_case):
    ring, case_id, headers = ring_case
    body = client.get(f"/query/{case_id}", headers=headers).json()
    m = body["nodes"][0]["members"][0]["mention_id"]
    ana = auth_headers(client, "B002", "pw2")
    assert client.get(f"/query/{case_id}", headers=ana).status_code == 200
    r = client.post(f"/query/{case_id}/resolution/overrides", headers=ana,
                    json={"mention_a_id": m, "mention_b_id": m + "x", "action": "never_merge"})
    assert r.status_code == 403


def test_cannot_split_identical_identifiers(client, ring_case):
    ring, case_id, headers = ring_case
    body = client.get(f"/query/{case_id}", headers=headers).json()
    phone = next(n for n in body["nodes"] if n["entity_type"] == "phone" and len(n["members"]) >= 2)
    a, b = phone["members"][0]["mention_id"], phone["members"][1]["mention_id"]
    r = client.post(f"/query/{case_id}/resolution/overrides", headers=headers,
                    json={"mention_a_id": a, "mention_b_id": b, "action": "never_merge"})
    assert r.status_code == 400


def test_influencers_are_people_and_ranked(client, ring_case):
    ring, case_id, headers = ring_case
    body = client.get(f"/query/{case_id}", headers=headers).json()
    assert 1 <= len(body["influencers"]) <= 5
    types = {n["id"]: n["entity_type"] for n in body["nodes"]}
    assert all(types[i["node_id"]] == "person" for i in body["influencers"])
    assert [i["rank"] for i in body["influencers"]] == list(range(1, len(body["influencers"]) + 1))
    assert "centrality" in body["influencers"][0]["reason"]


@pytest.mark.parametrize("fmt", ["markdown", "csv", "pdf"])
def test_report_contains_findings_not_just_counts(client, ring_case, fmt):
    ring, case_id, headers = ring_case
    gen = client.post(f"/reports/{case_id}/generate", json={"format": fmt}, headers=headers)
    assert gen.status_code == 201
    dl = client.get(f"/reports/{case_id}/{gen.json()['id']}/download", headers=headers)
    assert dl.status_code == 200
    if fmt == "pdf":
        assert dl.headers["content-type"] == "application/pdf"
        assert dl.content.startswith(b"%PDF") and len(dl.content) > 2000
        return
    text = dl.text
    if fmt == "markdown":
        assert "Key individuals" in text and "centrality" in text
        assert "Detected patterns" in text and "Suspicious" not in text.split("Detected patterns")[0]
        assert "Source documents:" in text
    else:
        assert text.splitlines()[0].startswith("entity,type,documents")
        assert len(text.splitlines()) > 20
