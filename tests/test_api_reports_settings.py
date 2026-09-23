"""
tests/test_api_reports_settings.py

Integration tests for the three routes added to back the previously-
placeholder Data Sources, Reports, and Settings pages:
- GET /ingest/{case_id}/summary        (api/routes/ingestion.py)
- POST/GET /reports/{case_id}...       (api/routes/reports.py)
- GET/PUT /settings/                   (api/routes/settings.py)
"""

from tests.conftest import auth_headers


def _create_case(client, headers, title="Test Case"):
    r = client.post("/cases/", json={"title": title, "description": "test case"}, headers=headers)
    assert r.status_code == 201
    return r.json()["id"]


def test_document_summary_reflects_ingested_documents(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")
    case_id = _create_case(client, headers)

    client.post("/ingest/", json={"id": "D1", "document_type": "fir", "raw_text": "text one", "case_id": case_id}, headers=headers)
    client.post("/ingest/", json={"id": "D2", "document_type": "fir", "raw_text": "text two", "case_id": case_id}, headers=headers)
    client.post("/ingest/", json={"id": "D3", "document_type": "cdr", "raw_text": "text three", "case_id": case_id}, headers=headers)

    r = client.get(f"/ingest/{case_id}/summary", headers=headers)
    assert r.status_code == 200
    sources = {s["document_type"]: s for s in r.json()["sources"]}
    assert sources["fir"]["count"] == 2
    assert sources["cdr"]["count"] == 1
    assert sources["fir"]["last_updated"] is not None


def test_document_summary_forbidden_for_unauthorized_case(client, seeded_users):
    owner_headers = auth_headers(client, "B001", "pw1")
    case_id = _create_case(client, owner_headers)

    other_headers = auth_headers(client, "B004", "pw4")  # admin in a different agency
    r = client.get(f"/ingest/{case_id}/summary", headers=other_headers)
    assert r.status_code == 403


def test_generate_list_and_download_report(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")
    case_id = _create_case(client, headers)
    client.post("/ingest/", json={"id": "D1", "document_type": "fir", "raw_text": "text", "case_id": case_id}, headers=headers)

    gen = client.post(f"/reports/{case_id}/generate", json={"format": "markdown"}, headers=headers)
    assert gen.status_code == 201
    report_id = gen.json()["id"]
    assert gen.json()["format"] == "markdown"
    assert "content" not in gen.json()  # list/generate responses never leak full content unintentionally

    listing = client.get(f"/reports/{case_id}", headers=headers)
    assert listing.status_code == 200
    assert any(r["id"] == report_id for r in listing.json()["reports"])

    download = client.get(f"/reports/{case_id}/{report_id}/download", headers=headers)
    assert download.status_code == 200
    assert "Case Summary" in download.text
    assert "fir" in download.text


def test_generate_report_rejects_invalid_format(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")
    case_id = _create_case(client, headers)
    r = client.post(f"/reports/{case_id}/generate", json={"format": "docx"}, headers=headers)
    assert r.status_code == 400


def test_settings_get_defaults_empty_and_put_merges(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")

    r = client.get("/settings/", headers=headers)
    assert r.status_code == 200
    assert r.json()["preferences"] == {}

    r = client.put("/settings/", json={"dark_mode": True}, headers=headers)
    assert r.status_code == 200
    assert r.json()["preferences"] == {"dark_mode": True}

    # A second, different key should merge in rather than replace.
    r = client.put("/settings/", json={"email_alerts": False}, headers=headers)
    assert r.json()["preferences"] == {"dark_mode": True, "email_alerts": False}
