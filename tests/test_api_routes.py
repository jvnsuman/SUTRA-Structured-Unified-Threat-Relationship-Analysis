"""
tests/test_api_routes.py

Integration tests for the ingestion, query, and case-management routes
— exercised through the real FastAPI app and a real (test) database.
Covers the request-validation, authorization, and agency-scoping
behavior each route is responsible for.
"""

from tests.conftest import auth_headers


def _new_case(client, headers, title="Test case"):
    return client.post("/cases/", json={"title": title, "description": "x"}, headers=headers).json()["id"]


def test_analyst_cannot_create_a_case(client, seeded_users):
    """Analysts are read-only by definition (SECURITY.md) -- they must
    not be able to originate a case, even with an otherwise-valid
    payload. Regression test: create_case previously only depended on
    get_current_user with no role check at all, so any authenticated
    user -- analyst included -- could create a case and be
    auto-assigned to it as an investigator.
    """
    ana = auth_headers(client, "B002", "pw2")
    r = client.post("/cases/", json={"title": "Analyst case", "description": "x"}, headers=ana)
    assert r.status_code == 403


def test_admin_cannot_create_a_case(client, seeded_users):
    """Admins manage accounts, not case content (SECURITY.md) -- same
    reasoning as the analyst check above.
    """
    adm = auth_headers(client, "B003", "pw3")
    r = client.post("/cases/", json={"title": "Admin case", "description": "x"}, headers=adm)
    assert r.status_code == 403


def test_super_admin_can_create_a_case(client, seeded_users):
    """SUPER_ADMIN is break-glass and may act as any role, including
    originating a case -- the create-case gate must not accidentally
    lock this out while closing the analyst/admin hole above.
    """
    sup = auth_headers(client, "B005", "pw5")
    r = client.post("/cases/", json={"title": "Super admin case", "description": "x"}, headers=sup)
    assert r.status_code == 201


def test_ingest_valid_document_returns_202(client, seeded_users):
    """Extraction is now real (spaCy-based) — a plain FIR-shaped
    document should come back "extracted" with an entity count, not
    the old permanently-stubbed "stored_pending_extraction" state.
    """
    headers = auth_headers(client, "B001", "pw1")
    case_id = _new_case(client, headers)
    r = client.post(
        "/ingest/",
        json={"id": "D1", "document_type": "fir", "raw_text": "Raju Kumar was seen near MG Road.", "case_id": case_id},
        headers=headers,
    )
    assert r.status_code == 202
    assert r.json()["status"] == "extracted"
    assert r.json()["entity_count"] >= 1
    assert r.json()["relation_count"] >= 1   # rule-based relations need no torch


def test_ingest_duplicate_document_returns_409(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")
    case_id = _new_case(client, headers)
    body = {"id": "D1", "document_type": "fir", "raw_text": "text", "case_id": case_id}
    client.post("/ingest/", json=body, headers=headers)
    r = client.post("/ingest/", json={**body, "raw_text": "text2"}, headers=headers)
    assert r.status_code == 409


def test_ingest_requires_case_id(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")
    r = client.post("/ingest/", json={"id": "D1", "document_type": "fir", "raw_text": "text"}, headers=headers)
    assert r.status_code == 400


def test_analyst_cannot_ingest_into_a_case(client, seeded_users):
    inv = auth_headers(client, "B001", "pw1")
    case_id = _new_case(client, inv)
    ana = auth_headers(client, "B002", "pw2")
    r = client.post("/ingest/", json={"id": "D1", "document_type": "fir", "raw_text": "x", "case_id": case_id}, headers=ana)
    assert r.status_code == 403


def test_unassigned_investigator_cannot_ingest_into_someone_elses_case(client, seeded_users, db_session):
    from db import repository as repo
    from schema.user import Role, User, hash_password
    repo.create_user(db_session, User(id="u9", name="Other", badge_id="B009", agency_id="AG1",
                                      role=Role.INVESTIGATOR, password_hash=hash_password("pw9")))
    inv = auth_headers(client, "B001", "pw1")
    case_id = _new_case(client, inv)
    other = auth_headers(client, "B009", "pw9")
    r = client.post("/ingest/", json={"id": "D1", "document_type": "fir", "raw_text": "x", "case_id": case_id}, headers=other)
    assert r.status_code == 403


def test_ingest_invalid_shape_returns_400(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")
    r = client.post("/ingest/", json={"id": "D2", "document_type": "tweet", "raw_text": "x"}, headers=headers)
    assert r.status_code == 400


def test_query_unauthorized_case_returns_403(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")
    r = client.get("/query/CASE-999", headers=headers)
    assert r.status_code == 403


def test_evidence_requires_auth(client, seeded_users):
    r = client.get("/evidence/E1")
    assert r.status_code == 401


def test_create_case_and_list(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")
    r = client.post("/cases/", json={"title": "Test case", "description": "test case"}, headers=headers)
    assert r.status_code == 201
    case_id = r.json()["id"]

    r = client.get("/cases/", headers=headers)
    assert r.status_code == 200
    assert any(c["id"] == case_id for c in r.json()["cases"])


def test_create_case_response_reflects_creator_auto_assignment(client, seeded_users):
    """create_case auto-assigns the creator as an investigator (see
    that route's docstring) — the response it returns must already
    reflect that, not the pre-assignment case object.
    """
    headers = auth_headers(client, "B001", "pw1")
    r = client.post("/cases/", json={"title": "Test case", "description": "test case"}, headers=headers)
    assert r.json()["assigned_investigator_ids"] == ["u1"]


def test_create_case_for_other_agency_forbidden(client, seeded_users):
    """Only a SUPER_ADMIN may create a case for an agency other than
    their own — an investigator cannot.
    """
    headers = auth_headers(client, "B001", "pw1")
    r = client.post("/cases/", json={"title": "x", "description": "test case", "agency_id": "AG2"}, headers=headers)
    assert r.status_code == 403


def test_assign_investigator_requires_admin_role(client, seeded_users):
    inv_headers = auth_headers(client, "B001", "pw1")
    r = client.post("/cases/", json={"title": "Test", "description": "test case"}, headers=inv_headers)
    case_id = r.json()["id"]

    r = client.post(f"/cases/{case_id}/assign", json={"user_id": "u2"}, headers=inv_headers)
    assert r.status_code == 403


def test_assign_investigator_cross_agency_forbidden(client, seeded_users):
    """An ADMIN from one agency cannot assign investigators on a case
    belonging to a different agency.
    """
    case_id = _new_case(client, auth_headers(client, "B001", "pw1"))

    other_agency_admin = auth_headers(client, "B004", "pw4")
    r = client.post(f"/cases/{case_id}/assign", json={"user_id": "u1"}, headers=other_agency_admin)
    assert r.status_code == 403


def test_assign_investigator_success(client, seeded_users):
    admin_headers = auth_headers(client, "B003", "pw3")
    case_id = _new_case(client, auth_headers(client, "B001", "pw1"))

    r = client.post(f"/cases/{case_id}/assign", json={"user_id": "u1"}, headers=admin_headers)
    assert r.status_code == 200
    assert "u1" in r.json()["assigned_investigator_ids"]


def test_set_case_status_by_assigned_investigator(client, seeded_users):
    """Unlike confidentiality, status is a working field the case's
    own assigned investigator (who is auto-assigned as its creator)
    should be able to update, not just an admin.
    """
    headers = auth_headers(client, "B001", "pw1")
    r = client.post("/cases/", json={"title": "Test", "description": "test case"}, headers=headers)
    case_id = r.json()["id"]
    assert r.json()["status"] == "open"

    r = client.post(f"/cases/{case_id}/status", json={"status": "under_review"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "under_review"


def test_set_case_status_by_unassigned_investigator_forbidden(client, seeded_users):
    """B001 creates (and is auto-assigned to) the case. B006 is a
    second investigator in the same agency who was never assigned to
    it, and must be rejected -- an investigator's write access is
    scoped to cases they're assigned to, not agency-wide.
    """
    case_id = _new_case(client, auth_headers(client, "B001", "pw1"))

    other_investigator = auth_headers(client, "B006", "pw6")
    r = client.post(f"/cases/{case_id}/status", json={"status": "closed"}, headers=other_investigator)
    assert r.status_code == 403


def test_set_case_status_by_scoped_admin(client, seeded_users):
    admin_headers = auth_headers(client, "B003", "pw3")
    case_id = _new_case(client, auth_headers(client, "B001", "pw1"))

    r = client.post(f"/cases/{case_id}/status", json={"status": "closed"}, headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "closed"


def test_set_case_status_cross_agency_admin_forbidden(client, seeded_users):
    case_id = _new_case(client, auth_headers(client, "B001", "pw1"))

    other_agency_admin = auth_headers(client, "B004", "pw4")
    r = client.post(f"/cases/{case_id}/status", json={"status": "closed"}, headers=other_agency_admin)
    assert r.status_code == 403


def test_set_case_status_invalid_value_returns_400(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")
    r = client.post("/cases/", json={"title": "Test", "description": "test case"}, headers=headers)
    case_id = r.json()["id"]

    r = client.post(f"/cases/{case_id}/status", json={"status": "archived"}, headers=headers)
    assert r.status_code == 400


def test_unassign_investigator_success(client, seeded_users):
    admin_headers = auth_headers(client, "B003", "pw3")
    case_id = _new_case(client, auth_headers(client, "B001", "pw1"))
    client.post(f"/cases/{case_id}/assign", json={"user_id": "u1"}, headers=admin_headers)
    # give the case a second investigator first, so removing "u1" doesn't
    # trip the last-investigator guard tested separately below
    client.post(f"/cases/{case_id}/assign", json={"user_id": "u6"}, headers=admin_headers)

    r = client.post(f"/cases/{case_id}/unassign", json={"user_id": "u1"}, headers=admin_headers)
    assert r.status_code == 200
    assert "u1" not in r.json()["assigned_investigator_ids"]


def test_unassign_last_investigator_forbidden(client, seeded_users):
    """The case's creator is auto-assigned on creation; removing them
    with no replacement would leave the case with zero investigators.
    """
    admin_headers = auth_headers(client, "B003", "pw3")
    inv_headers = auth_headers(client, "B001", "pw1")
    r = client.post("/cases/", json={"title": "Test", "description": "test case"}, headers=inv_headers)
    case_id = r.json()["id"]
    creator_id = r.json()["assigned_investigator_ids"][0]

    r = client.post(f"/cases/{case_id}/unassign", json={"user_id": creator_id}, headers=admin_headers)
    assert r.status_code == 400


def test_unassign_investigator_requires_admin_role(client, seeded_users):
    inv_headers = auth_headers(client, "B001", "pw1")
    case_id = _new_case(client, inv_headers)

    r = client.post(f"/cases/{case_id}/unassign", json={"user_id": "u1"}, headers=inv_headers)
    assert r.status_code == 403


def test_unassign_investigator_cross_agency_forbidden(client, seeded_users):
    admin_headers = auth_headers(client, "B003", "pw3")
    case_id = _new_case(client, auth_headers(client, "B001", "pw1"))
    client.post(f"/cases/{case_id}/assign", json={"user_id": "u1"}, headers=admin_headers)

    other_agency_admin = auth_headers(client, "B004", "pw4")
    r = client.post(f"/cases/{case_id}/unassign", json={"user_id": "u1"}, headers=other_agency_admin)
    assert r.status_code == 403
