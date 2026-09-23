# Security Policy

This system handles law-enforcement case data (FIRs, CDRs, financial
records, surveillance reports). Treat any suspected vulnerability as
sensitive.

## Reporting a vulnerability

Do not open a public GitHub issue for security problems. Instead,
contact the maintainer directly (see repository owner) with:

- A description of the issue and its potential impact
- Steps to reproduce
- Any relevant logs or payloads (redact real case data)

## Scope

- Authentication/session handling (`api/auth.py`, `schema/user.py`)
- Role/agency-based access control (`schema.user.authorize`,
  `schema.user.get_user_cases`)
- Data validation on ingestion (`schema/entities.py`,
  `api/routes/ingestion.py`)

## Implemented controls

- **Login brute-force protection** (`api/ratelimit.py`): 5 failures per badge id in 15 minutes lock that
  badge for 15 minutes; a per-IP limit (5x) blocks password spraying across badges. Locked accounts get
  `429` before any password check, and unknown badge ids are limited identically (no account
  enumeration). Configurable via `LOGIN_MAX_FAILURES`, `LOGIN_WINDOW_SECONDS`, `LOGIN_LOCKOUT_SECONDS`.
- **Password change signs out the account's other sessions.**
- **Audit log** (`audit_log` table, `GET /audit/`, admin-only): logins, failed logins, password changes,
  user creation, case creation/assignment/status/confidentiality changes, access-request approvals,
  document ingestion, resolution overrides, report generation. Append-only at the application layer;
  never stores passwords.
- **Case-scoped writes** (`api/permissions.py`): ingesting into a case and changing entity-resolution
  decisions require being an *assigned investigator* of that case (or super-admin). Analysts are read-only;
  admins manage accounts, not case content. (Ingestion previously accepted any authenticated user for any
  case id.)
- **Evidence access control**: `GET /evidence/{entity_id}` returns source documents only to users who can
  read the entity's case (previously any logged-in user could read any entity's evidence).
- **Input limits** on ingestion (text length, structured row count).
- **Tamper-evident ledger** (`ledger/chain.py`) for evidence-linking events.
- **Secrets**: `.env` is git-ignored; `.env.example` documents every setting. The Docker demo binds
  PostgreSQL to localhost only.

## Known limitations (current development stage)

- **Sessions and the login rate limiter are in-process memory.** With more than one API worker each keeps
  its own counters and session table. The provided Dockerfile runs one worker; move both to Redis (or
  another shared store) before scaling out. Sessions are lost on restart.
- **No encryption-at-rest** is configured; rely on the database/host for it.
- **Demo credentials**: `scripts/seed_data.py` creates accounts with well-known passwords and prints
  them. Never run it against a real deployment; the docker-compose database credentials are demo-only.
- **Session tokens are bearer tokens** in an `Authorization` header; there is no CSRF surface, but XSS
  in the dashboard would expose the token (stored in browser storage). Serve the dashboard over HTTPS
  with a strict CSP in any real deployment.
- **Audit-log tamper resistance is application-level.** An operator with direct database access can edit
  it; ship it to an append-only external sink for stronger guarantees.
- If a credential was ever shared (for example a `.env` inside a zip or chat), **rotate it**.

These are tracked as outstanding work, not accepted risk for a production deployment.
