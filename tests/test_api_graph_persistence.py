"""
tests/test_api_graph_persistence.py

Integration tests for the entity/relationship persistence wiring added
to api/routes/ingestion.py, api/routes/query.py, and
api/routes/alerts.py. Before this, ingestion extracted entities but
discarded them, so query always 501'd and alerts always reported an
empty list for every case, regardless of what was ingested. These
tests exercise the real path: ingest a document under a case, then
confirm the case's graph and alerts now reflect what was ingested.
"""

from tests.conftest import auth_headers


def _ingest_and_get_case(client, headers, raw_text: str, doc_id: str = "D1") -> str:
    """Create a case, ingest one document under it, and return the case_id."""
    case_id = client.post("/cases/", json={"title": "Persistence test case", "description": "test case"}, headers=headers).json()["id"]
    r = client.post(
        "/ingest/",
        json={"id": doc_id, "document_type": "fir", "case_id": case_id, "raw_text": raw_text},
        headers=headers,
    )
    assert r.status_code == 202, r.json()
    return case_id


def test_ingest_persists_entities_and_reports_relation_count(client, seeded_users):
    """The 202 response now includes relation_count alongside
    entity_count (0 with a clear detail message if transformers/torch
    aren't installed, rather than silently omitting it).
    """
    headers = auth_headers(client, "B001", "pw1")
    case_id = client.post("/cases/", json={"title": "Case A", "description": "test case"}, headers=headers).json()["id"]
    r = client.post(
        "/ingest/",
        json={
            "id": "D1",
            "document_type": "fir",
            "case_id": case_id,
            "raw_text": "Raju Kumar was seen near MG Road with Anita Sharma.",
        },
        headers=headers,
    )
    body = r.json()
    assert r.status_code == 202
    assert body["status"] == "extracted"
    assert body["entity_count"] >= 1
    assert "relation_count" in body


def test_query_returns_real_graph_after_ingestion(client, seeded_users):
    """Once a document has been ingested under a case, /query/{case_id}
    should return a real graph (matching dashboard/src/sampleData.js's
    shape) instead of the old permanent 501.
    """
    headers = auth_headers(client, "B001", "pw1")
    case_id = _ingest_and_get_case(
        client, headers, "Raju Kumar was seen near MG Road, contacted from 9876543210."
    )

    r = client.get(f"/query/{case_id}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["caseId"] == case_id
    assert body["stats"]["entitiesLinked"] >= 1
    assert len(body["nodes"]) == body["stats"]["entitiesLinked"]
    for node in body["nodes"]:
        # original contract fields plus the review/explainability metadata
        assert {"id", "label", "entity_type"} <= set(node.keys())
        assert {"needsReview", "mergeConfidence", "mergeReasons", "aliases", "members"} <= set(node.keys())


def test_query_still_returns_501_for_case_with_no_documents(client, seeded_users):
    """A case with zero ingested documents still gets the honest
    501 — persistence existing for *some* cases shouldn't fabricate a
    graph for a case that genuinely has nothing yet.
    """
    headers = auth_headers(client, "B001", "pw1")
    case_id = client.post("/cases/", json={"title": "Empty case", "description": "test case"}, headers=headers).json()["id"]

    r = client.get(f"/query/{case_id}", headers=headers)
    assert r.status_code == 501


def test_alerts_reflect_real_ingested_case_data(client, seeded_users):
    """alerts.py should now run graph.analytics.detect_anomalies
    against a real, case-scoped graph once entities are persisted,
    instead of always returning an empty list.
    """
    headers = auth_headers(client, "B001", "pw1")
    case_id = _ingest_and_get_case(
        client, headers, "Raju Kumar was seen near MG Road, contacted from 9876543210."
    )

    r = client.get(f"/alerts/{case_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["case_id"] == case_id
    # Not asserting alerts is non-empty: hub-and-spoke needs enough
    # nodes/degree variance to trigger, which one short document won't
    # produce. The point is the route runs the real check without
    # erroring, not that it always finds something.
    assert isinstance(r.json()["alerts"], list)
