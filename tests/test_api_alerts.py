"""
tests/test_api_alerts.py

Integration tests for api/routes/alerts.py — the new Recent Alerts
feed backing the sidebar-nav dashboard. Covers authorization (same
case-scoping as /query) and the honest-empty-state behavior (no
findings fabricated when nothing is persisted yet — see that module's
docstring).
"""

from tests.conftest import auth_headers


def test_alerts_requires_auth(client, seeded_users):
    r = client.get("/alerts/CASE-1")
    assert r.status_code == 401


def test_alerts_unauthorized_case_returns_403(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")
    r = client.get("/alerts/CASE-999", headers=headers)
    assert r.status_code == 403


def test_alerts_authorized_case_returns_empty_list_honestly(client, seeded_users):
    """No entity/relationship persistence exists yet (see
    api/routes/query.py's docstring for the same limitation) — the
    alerts endpoint must report an empty list, not fabricate example
    alerts, for a case it has nothing to analyze.
    """
    headers = auth_headers(client, "B001", "pw1")
    r = client.post("/cases/", json={"title": "Alert test case", "description": "test case"}, headers=headers)
    case_id = r.json()["id"]

    r = client.get(f"/alerts/{case_id}", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"case_id": case_id, "alerts": []}


def test_alert_ids_are_unique(client, seeded_users):
    """Aggregate findings (no doc/node id) used to collide on one id, which
    broke the dashboard's React list keys (found by driving the UI in a browser)."""
    from data.trafficking_scenario import generate_trafficking_ring
    from tests.conftest import auth_headers

    headers = auth_headers(client, "B001", "pw1")
    case_id = client.post("/cases/", json={"title": "ring", "description": "x"}, headers=headers).json()["id"]
    for d in generate_trafficking_ring(seed=26189).documents:
        kind = {"FIR": "fir", "CDR": "cdr", "FINANCIAL_RECORD": "financial"}.get(d.doc_type.name, "fir")
        client.post("/ingest/", headers=headers, json={
            "id": d.doc_id, "document_type": kind, "case_id": case_id,
            "raw_text": d.text or f"Structured {kind} record {d.doc_id}", "structured": d.structured or {}})
    alerts = client.get(f"/alerts/{case_id}", headers=headers).json()["alerts"]
    ids = [a["id"] for a in alerts]
    assert len(alerts) >= 3 and len(ids) == len(set(ids))
