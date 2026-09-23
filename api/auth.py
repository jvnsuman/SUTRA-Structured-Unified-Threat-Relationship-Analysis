"""
api/auth.py

HTTP-layer authentication/authorization. Wraps the domain login logic
(schema.user.verify_password) and DB lookups (db.repository) with
session/token handling for the API.

Keep this separate from schema/user.py: schema/user.py defines what a
User and Role are; this file defines how the API checks them on each
request (headers, tokens, session lifetime).
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from api.ratelimit import login_limiter
from config import get_settings
from db import repository as repo
from db.connection import get_db
from schema.user import Role, User, authorize as schema_authorize, hash_password, verify_password

# Session tokens are intentionally kept in memory, not the database —
# they're ephemeral by design (an 8-hour TTL by default). token ->
# (badge_id, expires_at).
_SESSION_STORE: dict[str, tuple[str, datetime]] = {}
_SESSION_TTL = timedelta(hours=get_settings().session_ttl_hours)


def login_endpoint(credentials: dict, db: Session, ip_address: str = "unknown") -> dict:
    """Validate {"badge_id", "password"} against the database and, on
    success, issue a new session token. Raises HTTPException(401) on
    any failure — deliberately the same error for "unknown badge" and
    "wrong password", so a caller can't enumerate valid badge IDs.

    Returns the same user fields as GET /auth/me (name, role, id,
    badge_id, agency_id) alongside the token, so the dashboard's
    session object has a consistent shape whether it just came from a
    fresh login or from restoring an existing one on page reload.
    """
    if not isinstance(credentials, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed request body")

    badge_id = credentials.get("badge_id")
    password = credentials.get("password")
    if not isinstance(badge_id, str) or not isinstance(password, str) or not badge_id or not password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid badge ID or password")

    # Locked badge/address -> 429 BEFORE any password check, so a locked
    # account can't be used as a password oracle.
    login_limiter.check(badge_id, ip_address)

    user = repo.get_user_by_badge_id(db, badge_id)
    if user is None or user.password_hash is None or not verify_password(password, user.password_hash):
        login_limiter.record_failure(badge_id, ip_address)
        repo.append_audit(db, "login_failed", actor_badge_id=badge_id, ip_address=ip_address, success=False)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid badge ID or password")

    login_limiter.record_success(badge_id, ip_address)
    repo.append_audit(db, "login", actor=user, ip_address=ip_address)
    repo.update_last_login(db, badge_id, datetime.now(timezone.utc).isoformat())

    token = secrets.token_urlsafe(32)
    _SESSION_STORE[token] = (user.badge_id, datetime.now(timezone.utc) + _SESSION_TTL)

    return {
        "token": token,
        "token_type": "bearer",
        "expires_in_seconds": int(_SESSION_TTL.total_seconds()),
        "name": user.name,
        "role": user.role.value,
        "id": user.id,
        "badge_id": user.badge_id,
        "agency_id": user.agency_id,
    }


def logout_endpoint(token: str) -> None:
    """Invalidate a session token. No-op (not an error) if it's
    already invalid/expired — logging out twice should never fail.
    """
    _SESSION_STORE.pop(token, None)


def change_password_endpoint(user: User, data: dict, db: Session, keep_token: Optional[str] = None) -> dict:
    """Self-service password change for the currently logged-in user
    (see api/main.py's POST /auth/change-password). Requires the
    correct current password — same "don't tell them which part was
    wrong" spirit as login_endpoint, though here it's simpler since
    we already know the badge_id belongs to `user`.
    """
    current_password = data.get("current_password")
    new_password = data.get("new_password")
    if not current_password or not new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="current_password and new_password are required"
        )
    if user.password_hash is None or not verify_password(current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
    if len(new_password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must be at least 8 characters")

    repo.update_password_hash(db, user.id, hash_password(new_password, iterations=get_settings().pbkdf2_iterations))
    # Sign out every OTHER session of this account: a stolen token must
    # not survive the password change that was meant to lock it out.
    for tok in [t for t, (badge, _) in _SESSION_STORE.items() if badge == user.badge_id and t != keep_token]:
        _SESSION_STORE.pop(tok, None)
    repo.append_audit(db, "password_changed", actor=user, target_type="user", target_id=user.id)
    return {"status": "password_changed"}


def _resolve_token(db: Session, token: str) -> Optional[User]:
    """Look up the User behind a session token, checking expiry and
    re-fetching the live user record (not a cached copy) so a role
    change or deactivation takes effect on the very next request.
    """
    entry = _SESSION_STORE.get(token)
    if entry is None:
        return None
    badge_id, expires_at = entry
    if datetime.now(timezone.utc) > expires_at:
        _SESSION_STORE.pop(token, None)
        return None
    user = repo.get_user_by_badge_id(db, badge_id)
    if user is None:
        _SESSION_STORE.pop(token, None)
        return None
    return user


def get_current_user(
    authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db)
) -> User:
    """FastAPI dependency — resolves the Bearer token in the
    Authorization header into a User, or raises 401. Use as
    `user: User = Depends(get_current_user)` in any route that
    requires the caller to be logged in.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header (expected 'Bearer <token>')",
        )
    token = authorization.split(" ", 1)[1].strip()
    user = _resolve_token(db, token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session token")
    return user


def require_role(required_role: Role):
    """Dependency factory for role-gated routes — builds on
    get_current_user, then checks schema.user.authorize before
    allowing the request through.

    Usage: `user: User = Depends(require_role(Role.ADMIN))`
    """

    def _dependency(user: User = Depends(get_current_user)) -> User:
        if not schema_authorize(user, required_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the '{required_role.value}' role",
            )
        return user

    return _dependency
