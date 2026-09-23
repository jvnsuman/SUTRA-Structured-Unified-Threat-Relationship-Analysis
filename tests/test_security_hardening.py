"""Login brute-force protection, audit log, and session hygiene."""

from api.ratelimit import login_limiter
from tests.conftest import auth_headers


def _login(client, badge, pw):
    return client.post("/auth/login", json={"badge_id": badge, "password": pw})


def test_lockout_after_repeated_failures_even_with_the_right_password(client, seeded_users):
    for _ in range(5):
        assert _login(client, "B001", "wrong").status_code == 401
    r = _login(client, "B001", "pw1")               # correct password, but locked
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_lockout_is_per_badge_not_global(client, seeded_users):
    for _ in range(5):
        _login(client, "B001", "wrong")
    assert _login(client, "B002", "pw2").status_code == 200


def test_unknown_badge_is_rate_limited_the_same_way(client, seeded_users):
    for _ in range(5):
        assert _login(client, "NOPE", "x").status_code == 401
    assert _login(client, "NOPE", "x").status_code == 429


def test_successful_login_clears_the_failure_count(client, seeded_users):
    for _ in range(4):
        _login(client, "B001", "wrong")
    assert _login(client, "B001", "pw1").status_code == 200
    for _ in range(4):
        assert _login(client, "B001", "wrong").status_code == 401
    login_limiter.reset()


def test_non_string_credentials_are_rejected_not_crashed(client, seeded_users):
    assert client.post("/auth/login", json={"badge_id": ["a"], "password": {"x": 1}}).status_code == 401


def test_password_change_signs_out_other_sessions(client, seeded_users):
    first = auth_headers(client, "B001", "pw1")
    second = auth_headers(client, "B001", "pw1")
    r = client.post("/auth/change-password", json={"current_password": "pw1", "new_password": "newpass123"}, headers=second)
    assert r.status_code == 200
    assert client.get("/auth/me", headers=second).status_code == 200      # current session survives
    assert client.get("/auth/me", headers=first).status_code == 401       # the other one does not


def test_audit_log_records_logins_failures_and_admin_actions(client, seeded_users):
    _login(client, "B001", "wrong")
    admin = auth_headers(client, "B003", "pw3")
    inv = auth_headers(client, "B001", "pw1")
    client.post("/cases/", json={"title": "T", "description": "d"}, headers=inv)
    entries = client.get("/audit/", headers=admin).json()["entries"]
    actions = {e["action"] for e in entries}
    assert {"login", "login_failed", "case_created"} <= actions
    failed = next(e for e in entries if e["action"] == "login_failed")
    assert failed["success"] is False and failed["actor_badge_id"] == "B001"


def test_audit_log_is_admin_only(client, seeded_users):
    assert client.get("/audit/", headers=auth_headers(client, "B001", "pw1")).status_code == 403
    assert client.get("/audit/", headers=auth_headers(client, "B002", "pw2")).status_code == 403
    assert client.get("/audit/", headers=auth_headers(client, "B003", "pw3")).status_code == 200
    assert client.get("/audit/").status_code == 401


def test_audit_log_never_stores_passwords(client, seeded_users):
    _login(client, "B001", "supersecretwrong")
    admin = auth_headers(client, "B003", "pw3")
    assert "supersecretwrong" not in client.get("/audit/", headers=admin).text


def test_ingest_size_limits(client, seeded_users):
    h = auth_headers(client, "B001", "pw1")
    case_id = client.post("/cases/", json={"title": "T", "description": "d"}, headers=h).json()["id"]
    r = client.post("/ingest/", json={"id": "big", "document_type": "fir", "case_id": case_id,
                                      "raw_text": "x" * 200_001}, headers=h)
    assert r.status_code == 413
