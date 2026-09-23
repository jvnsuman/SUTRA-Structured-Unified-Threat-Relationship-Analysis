"""
schema/user.py

System-level User, Role, and Agency models — who logs in and uses the
dashboard, and which real-world law-enforcement agency they belong to.

Kept deliberately separate from schema.entities.Entity (EntityType.PERSON):
a User is someone who operates the software; a Person entity is a
suspect/case subject extracted from evidence. Mixing the two would be
both a data-integrity bug and a privacy problem.

The stores below (_USER_STORE, _AGENCY_STORE, _CASE_ASSIGNMENTS,
_CASE_AGENCY, _ALL_CASE_IDS) are an in-memory reference implementation
of this module's pure business logic (hashing, login, role hierarchy,
case scoping). The live API (api/auth.py, api/routes/) now reads and
writes through db/repository.py instead, which persists the same
shapes to a real database. These in-memory functions still matter:
they're unit-tested directly (tests/test_schema_user.py) as the
source of truth for the authorization rules themselves, independent
of how they're persisted.
"""

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class Role(str, Enum):
    """The four account roles. Higher-privilege roles are a superset of
    the roles below them (see _ROLE_IMPLIES) with one exception:
    INVESTIGATOR is its own lane, not a subset of ANALYST.
    """

    INVESTIGATOR = "investigator"   # view + edit own assigned cases, full dashboard access
    ANALYST = "analyst"             # cross-case read-only, scoped to their own agency
    ADMIN = "admin"                  # manage accounts + audit logs, scoped to their own agency
    SUPER_ADMIN = "super_admin"      # cross-agency: manage every agency/account, full audit access


@dataclass
class Agency:
    """A real-world law-enforcement agency/department a User belongs to
    (e.g. NCRB, a state police cybercrime cell, Women Safety Division).

    Not to be confused with schema.entities.EntityType.ORGANIZATION,
    which represents a suspect-side organization (e.g. a trafficking
    ring) pulled from evidence, not a real investigating agency.
    """

    id: str
    name: str
    agency_type: str  # e.g. "ncrb", "state_police", "women_safety_division"
    parent_agency_id: Optional[str] = None  # for hierarchical/regional structure
    is_active: bool = True


@dataclass
class User:
    """A person who logs into the dashboard/API."""

    id: str
    name: str
    badge_id: str
    agency_id: str  # references Agency.id
    role: Role
    password_hash: Optional[str] = None  # or an SSO token, once that integration exists
    last_login: Optional[str] = None
    preferences: dict = field(default_factory=dict)  # Settings page toggles (dark_mode, email_alerts, auto_refresh_graph, ...) — an open key-value bag, not a fixed schema


_HASH_ALGO = "pbkdf2_sha256"
_HASH_ITERATIONS = 260_000


def hash_password(password: str, iterations: int = _HASH_ITERATIONS) -> str:
    """Hash a plaintext password for storage in User.password_hash.

    Uses PBKDF2-HMAC-SHA256 via the stdlib hashlib (no extra
    dependency for something this security-sensitive). Encodes the
    algorithm, iteration count, and salt into the stored string so
    verify_password can check it later without needing them passed
    in separately. `iterations` defaults to this module's constant
    but callers on the live path pass config.get_settings().pbkdf2_iterations
    instead, so PBKDF2_ITERATIONS in .env actually takes effect —
    verify_password reads the count back out of the stored hash, so
    existing hashes made at a different iteration count still verify.
    """
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_HASH_ALGO}${iterations}${salt.hex()}${derived.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a hash produced by hash_password.

    Uses a constant-time comparison (hmac.compare_digest) to avoid
    leaking information about how close a guess was via response
    timing. Returns False (rather than raising) on any malformed
    input, since a corrupted stored hash should fail auth, not crash it.
    """
    try:
        algo, iterations_str, salt_hex, hash_hex = password_hash.split("$")
    except (ValueError, AttributeError):
        return False
    if algo != _HASH_ALGO:
        return False
    iterations = int(iterations_str)
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(hash_hex)
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


# In-memory reference stores — see the module docstring. The live API
# persists through db/repository.py instead; these back this module's
# own functions and their unit tests.
_USER_STORE: dict[str, User] = {}             # badge_id -> User
_AGENCY_STORE: dict[str, Agency] = {}         # agency.id -> Agency
_CASE_ASSIGNMENTS: dict[str, list[str]] = {}  # user.id -> [case_id, ...]
_CASE_AGENCY: dict[str, str] = {}             # case_id -> agency_id it belongs to
_ALL_CASE_IDS: list[str] = []                 # every known case_id, for SUPER_ADMIN


def register_agency(agency: Agency) -> None:
    """Add an agency to the in-memory store."""
    _AGENCY_STORE[agency.id] = agency


def get_agency(user: User) -> Optional[Agency]:
    """Look up the Agency a user belongs to, or None."""
    return _AGENCY_STORE.get(user.agency_id) if user else None


def register_user(user: User) -> None:
    """Add a user to the in-memory store, keyed by badge_id."""
    _USER_STORE[user.badge_id] = user


def get_user_by_badge_id(badge_id: str) -> Optional[User]:
    """Look up a registered user by badge_id, or None. Public read
    access to the store, so callers (e.g. session/token layers) don't
    need to reach into _USER_STORE directly.
    """
    return _USER_STORE.get(badge_id)


def assign_case(user_id: str, case_id: str, agency_id: Optional[str] = None) -> None:
    """Record that a user is assigned to a case, and (if given) which
    agency the case belongs to. The agency_id is what makes
    ANALYST/ADMIN cross-case visibility agency-scoped rather than
    global — see get_user_cases below.
    """
    _CASE_ASSIGNMENTS.setdefault(user_id, [])
    if case_id not in _CASE_ASSIGNMENTS[user_id]:
        _CASE_ASSIGNMENTS[user_id].append(case_id)
    if case_id not in _ALL_CASE_IDS:
        _ALL_CASE_IDS.append(case_id)
    if agency_id is not None:
        _CASE_AGENCY[case_id] = agency_id


def login(credentials: dict) -> Optional[User]:
    """Authenticate against the in-memory store and return the User, or
    None on any failure. Expects credentials = {"badge_id": str,
    "password": str}. Deliberately returns None rather than raising or
    distinguishing "unknown badge" from "wrong password" — a caller
    should never be able to tell which one happened, since that would
    let an attacker enumerate valid badge IDs.
    """
    badge_id = credentials.get("badge_id")
    password = credentials.get("password")
    if not badge_id or not password:
        return None

    user = _USER_STORE.get(badge_id)
    if user is None or user.password_hash is None:
        return None

    if not verify_password(password, user.password_hash):
        return None

    user.last_login = datetime.now(timezone.utc).isoformat()
    return user


# Which roles a given role is allowed to act as, for authorize() below.
# ANALYST and ADMIN both cover cross-case read access (scoped to their
# own agency); ADMIN additionally covers account/audit management
# (also agency-scoped); SUPER_ADMIN covers everything, across every
# agency. Below SUPER_ADMIN these are otherwise independent, not a
# strict ladder — INVESTIGATOR is not a subset of ANALYST or vice versa.
_ROLE_IMPLIES: dict[Role, set[Role]] = {
    Role.SUPER_ADMIN: {Role.SUPER_ADMIN, Role.ADMIN, Role.ANALYST},
    Role.ADMIN: {Role.ADMIN, Role.ANALYST},
    Role.ANALYST: {Role.ANALYST},
    Role.INVESTIGATOR: {Role.INVESTIGATOR},
}


def authorize(user: User, required_role: Role) -> bool:
    """Check whether a user's role permits an action gated on required_role,
    per the _ROLE_IMPLIES hierarchy above.
    """
    if user is None:
        return False
    return required_role in _ROLE_IMPLIES.get(user.role, {user.role})


def get_user_cases(user: User) -> list:
    """Return the case IDs this user can access, scoped by role:

    - INVESTIGATOR: only cases explicitly assigned to them.
    - ANALYST / ADMIN: every case belonging to their own agency
      (cross-case, but not cross-agency).
    - SUPER_ADMIN: every known case, across every agency.
    """
    if user is None:
        return []
    if user.role == Role.SUPER_ADMIN:
        return list(_ALL_CASE_IDS)
    if user.role in (Role.ANALYST, Role.ADMIN):
        return [
            case_id for case_id in _ALL_CASE_IDS
            if _CASE_AGENCY.get(case_id) == user.agency_id
        ]
    return list(_CASE_ASSIGNMENTS.get(user.id, []))
