"""
tests/test_api_users.py

Integration tests for GET /users/lookup/{badge_id} (api/routes/users.py)
— the admin-only lookup the dashboard's Admin page uses to resolve a
badge ID into a user_id before calling POST /cases/{case_id}/assign.
"""

from tests.conftest import auth_headers


def test_create_user_requires_admin_role(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")  # investigator
    r = client.post(
        "/users/", json={"name": "New Inv", "badge_id": "B999", "password": "a-strong-password", "role": "investigator"}, headers=headers
    )
    assert r.status_code == 403


def test_admin_can_create_investigator_in_own_agency(client, seeded_users):
    headers = auth_headers(client, "B003", "pw3")  # admin, AG1
    r = client.post(
        "/users/", json={"name": "New Inv", "badge_id": "B999", "password": "a-strong-password", "role": "investigator"}, headers=headers
    )
    assert r.status_code == 201
    body = r.json()
    assert body["badge_id"] == "B999"
    assert body["agency_id"] == "AG1"

    # And the new account can actually log in.
    r = client.post("/auth/login", json={"badge_id": "B999", "password": "a-strong-password"})
    assert r.status_code == 200


def test_admin_cannot_create_admin_account(client, seeded_users):
    headers = auth_headers(client, "B003", "pw3")  # admin
    r = client.post(
        "/users/", json={"name": "New Admin", "badge_id": "B999", "password": "a-strong-password", "role": "admin"}, headers=headers
    )
    assert r.status_code == 403


def test_admin_cannot_create_user_in_other_agency(client, seeded_users):
    headers = auth_headers(client, "B003", "pw3")  # admin, AG1
    r = client.post(
        "/users/",
        json={"name": "New Inv", "badge_id": "B999", "password": "a-strong-password", "role": "investigator", "agency_id": "AG2"},
        headers=headers,
    )
    assert r.status_code == 403


def test_super_admin_can_create_admin_in_any_agency(client, seeded_users):
    headers = auth_headers(client, "B005", "pw5")  # super_admin, AG1
    r = client.post(
        "/users/",
        json={"name": "New Admin", "badge_id": "B999", "password": "a-strong-password", "role": "admin", "agency_id": "AG2"},
        headers=headers,
    )
    assert r.status_code == 201
    assert r.json()["agency_id"] == "AG2"


def test_create_user_duplicate_badge_id_is_409(client, seeded_users):
    headers = auth_headers(client, "B003", "pw3")  # admin
    r = client.post(
        "/users/", json={"name": "Dup", "badge_id": "B001", "password": "a-strong-password", "role": "investigator"}, headers=headers
    )
    assert r.status_code == 409


def test_create_user_short_password_rejected(client, seeded_users):
    headers = auth_headers(client, "B003", "pw3")  # admin
    r = client.post("/users/", json={"name": "New Inv", "badge_id": "B999", "password": "short", "role": "investigator"}, headers=headers)
    assert r.status_code == 400


def test_create_user_invalid_role_rejected(client, seeded_users):
    headers = auth_headers(client, "B003", "pw3")  # admin
    r = client.post(
        "/users/", json={"name": "New Inv", "badge_id": "B999", "password": "a-strong-password", "role": "wizard"}, headers=headers
    )
    assert r.status_code == 400
    headers = auth_headers(client, "B001", "pw1")  # investigator
    r = client.get("/users/lookup/B002", headers=headers)
    assert r.status_code == 403


def test_lookup_requires_auth(client, seeded_users):
    r = client.get("/users/lookup/B002")
    assert r.status_code == 401


def test_admin_can_look_up_user_in_own_agency(client, seeded_users):
    headers = auth_headers(client, "B003", "pw3")  # admin, AG1
    r = client.get("/users/lookup/B001", headers=headers)  # investigator, AG1
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "u1"
    assert body["badge_id"] == "B001"
    assert body["agency_id"] == "AG1"
    assert body["role"] == "investigator"


def test_admin_cannot_look_up_user_in_other_agency(client, seeded_users):
    headers = auth_headers(client, "B003", "pw3")  # admin, AG1
    r = client.get("/users/lookup/B004", headers=headers)  # admin_other_agency, AG2
    assert r.status_code == 403


def test_super_admin_can_look_up_user_in_any_agency(client, seeded_users):
    headers = auth_headers(client, "B005", "pw5")  # super_admin, AG1
    r = client.get("/users/lookup/B004", headers=headers)  # admin_other_agency, AG2
    assert r.status_code == 200
    assert r.json()["id"] == "u4"


def test_lookup_unknown_badge_id_is_404(client, seeded_users):
    headers = auth_headers(client, "B003", "pw3")  # admin
    r = client.get("/users/lookup/NOPE", headers=headers)
    assert r.status_code == 404


def test_get_user_by_id_requires_admin_role(client, seeded_users):
    headers = auth_headers(client, "B001", "pw1")  # investigator
    r = client.get("/users/u2", headers=headers)
    assert r.status_code == 403


def test_admin_can_get_user_by_id_in_own_agency(client, seeded_users):
    headers = auth_headers(client, "B003", "pw3")  # admin, AG1
    r = client.get("/users/u1", headers=headers)  # investigator, AG1
    assert r.status_code == 200
    assert r.json()["badge_id"] == "B001"


def test_admin_cannot_get_user_by_id_in_other_agency(client, seeded_users):
    headers = auth_headers(client, "B003", "pw3")  # admin, AG1
    r = client.get("/users/u4", headers=headers)  # admin_other_agency, AG2
    assert r.status_code == 403


def test_get_unknown_user_id_is_404(client, seeded_users):
    headers = auth_headers(client, "B003", "pw3")  # admin
    r = client.get("/users/nope", headers=headers)
    assert r.status_code == 404
