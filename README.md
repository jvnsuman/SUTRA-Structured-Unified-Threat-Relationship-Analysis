# SUTRA — Structured Unified Threat & Relationship Analysis

**Smart India Hackathon — Problem Statement SIH26189 (AI-Powered Criminal Network Analysis System)**
**Org/Dept:** Ministry of Home Affairs — NCRB, Women Safety Division
**Theme:** Blockchain & Cybersecurity | **Category:** Software

A system that ingests fragmented, unstructured law-enforcement data (FIRs,
CDRs, financial records, surveillance, social media, criminal history,
intel reports), extracts entities, builds a relationship graph, identifies
key influencers, detects suspicious patterns, and gives investigators an
interactive dashboard to drill from a flagged pattern down to the exact
source evidence behind it.

> **Scope boundary:** this is network analysis of **known, already-open
> cases** — not predictive policing. The system flags network *positions*
> (high centrality, bridging roles) within a case's existing entity set; it
> does not score individuals outside an active investigation. Every flag
> traces back to source evidence, and the investigator makes the final
> call, not the algorithm. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
> for the full reasoning.

---

## Table of contents

- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
  - [Option A — Docker](#option-a--docker-simplest)
  - [Option B — Local development](#option-b--local-development)
- [Roles & permissions](#roles--permissions)
- [API surface](#api-surface)
- [Testing](#testing)
- [Measured quality](#measured-quality)
- [Current implementation status](#current-implementation-status)
- [Known limitations](#known-limitations)
- [Security](#security)
- [License](#license)

---

## Architecture

A 7-stage pipeline, with two cross-cutting concerns:

1. **Data ingestion** — multi-source adapters (FIR text, CDR, financial, social, intel)
2. **NLP entity extraction** — people, phones, locations, organizations, vehicles
3. **Entity resolution** — merge aliases, scripts, and partial matches across documents
4. **Graph construction** — typed nodes and edges (NetworkX)
5. **Graph analytics** — centrality, community detection, anomaly detection, link prediction
6. **Explainability** — every flag traces back to the source document(s) behind it
7. **Investigator dashboard** — interactive graph UI with drill-down

*Cross-cutting:* a synthetic data generator feeds stage 1 for development/demo
purposes without touching real case data; ethics guardrails (no individual
risk-scoring, no predictive-policing framing) constrain stage 5; a
hash-chained integrity ledger (stage 8, see below) makes tampering with the
evidence trail detectable.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the entity schema
(Person / Phone / Location / Vehicle / Organization / Event) and how
traceability back to raw source records is preserved end to end.

### The "blockchain" piece

`ledger/chain.py` is a hash-chained, append-only integrity ledger — every
entry is hashed together with the previous entry's hash (`entry_hash =
SHA-256(sequence_number ‖ event_type ‖ payload_json ‖ prev_hash)`), so
editing, deleting, or reordering any past entry breaks every hash after it.
`verify_chain()` walks the whole chain and reports the first broken link, if
any. It deliberately is **not** a distributed, multi-node blockchain — a
single-agency investigative platform has no multi-party trust problem that a
peer-to-peer consensus protocol would solve — but it is the specific
tamper-evidence pattern that matters for an evidence trail a prosecutor
might rely on. It records case creation, evidence-linking events, and
access-request resolutions; it's a parallel audit trail, not the primary
store for that data (`GET /ledger/entries`, `GET /ledger/verify`).

## Tech stack

| Layer | Stack |
|---|---|
| NLP entity extraction | spaCy (`en_core_web_sm`) + an Indian-FIR rule layer (`nlp/indian_rules.py`, `nlp/gazetteer.py`): alias markers (`alias`/`@`/`urf`), honorifics, `r/o`, plates, phone/UPI/account formats, ALL-CAPS text, Devanagari names/places/digits |
| Entity resolution | Multi-signal, explainable: phonetic + token-aligned name similarity across scripts (`nlp/translit.py`), explicit-alias, shared phone/vehicle, shared context; constraint-aware clustering with a surname veto; merges in the 0.60–0.80 confidence band are flagged for investigator review; investigator overrides (split / confirm) persist |
| Relation extraction | Deterministic rules by default (`nlp/relation_rules.py`, no model needed); optional zero-shot NLI layer (`requirements-ml.txt`, `USE_ZERO_SHOT_RELATIONS=1`) |
| Structured records | CDR / financial rows parsed straight into entities and edges (`nlp/structured.py`) |
| Graph | NetworkX — chosen over Neo4j deliberately (Neo4j Community is GPLv3, incompatible with this project's Apache 2.0 + permissive-license stack; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)) |
| Integrity ledger | Hash-chained (`ledger/chain.py`, SHA-256), persisted via `db.models` and `db/repository.py` |
| Backend / API | FastAPI, SQLAlchemy, PostgreSQL (SQLite supported for tests), Alembic migrations |
| Frontend | React + Vite, Cytoscape.js (graph rendering) |
| Synthetic data | Faker |
| Reports | Markdown, CSV, PDF (`api/report_builder.py`, reportlab) |
| Testing / quality | pytest + pytest-cov, httpx (FastAPI `TestClient`), ruff, a P/R/F1 evaluation harness (`scripts/evaluate.py`) with a regression guard in CI |

## Project structure

```
schema/          Domain models — Entity/Relationship types, Case, User/Role, SourceDocument, Report
nlp/             pipeline.py (the one document -> entities+relations path) = extraction.py (+ indian_rules.py,
                 gazetteer.py, translit.py, entities.py) -> resolution.py (+ confidence.py) ->
                 relation_rules.py / structured.py (relation_classification.py = optional zero-shot layer)
graph/           build.py (NetworkX construction + centrality), analytics.py (centrality, Louvain, anomaly
                 detection, link prediction), explainability.py (in-memory evidence index; the durable
                 store is db.models.EvidenceLinkORM)
ledger/          chain.py — hash-chained, append-only integrity ledger (case creation, evidence links,
                 access-request resolutions)
db/              models.py (SQLAlchemy ORM), repository.py (all persistence/query functions), connection.py
alembic/         Database migrations (baseline schema + 7 incremental migrations, checked in CI)
api/             FastAPI app (main.py, auth.py, permissions.py, ratelimit.py, casegraph.py,
                 report_builder.py) + routes/ (cases, ingestion, query, evidence, alerts, reports,
                 settings, users, audit, ledger, access_requests, cross_case)
dashboard/       React + Vite frontend (sidebar-nav layout: Sidebar, TopBar, GraphCanvas, EvidencePanel,
                 IngestionForm, AlertsFeed, MetricStrip, NetworkOverview, per-role pages including
                 Admin, CrossCaseMatches, LedgerIntegrity)
data/            generate_synthetic.py — generic synthetic records; trafficking_scenario.py — a planted
                 trafficking ring generator WITH ground truth; eval/ — hand-labelled evaluation set
scripts/         seed_data.py (demo agencies/users/case), populate_demo_case.py (ingest synthetic
                 documents into a real case; --scenario trafficking plants the ring), evaluate.py
                 (P/R/F1 measurement harness)
tests/           259 pytest cases — schema, NLP + Indian-text rules, transliteration, multi-signal
                 resolution, structured records + relations, graph/analytics, ledger, security
                 hardening, migrations, API routes/auth/users, a planted-trafficking-ring end-to-end
                 test, and an evaluation-regression guard
docs/            ARCHITECTURE.md, EVALUATION.md (measured quality + known failure modes)
```

## Getting started

### Option A — Docker (simplest)

```bash
docker compose up --build
```

This starts PostgreSQL (bound to `127.0.0.1:5432` only — never exposed
beyond localhost, since it ships with demo credentials), the FastAPI
backend (`8000`), and the dashboard, served via nginx (`4173`, which also
reverse-proxies `/api` to the backend — see `dashboard/nginx.conf`).

### Option B — Local development

**Backend:**
```bash
pip install -r requirements.txt          # core; no torch needed
# optional heavy extras (zero-shot relations, multilingual NER): pip install -r requirements-ml.txt

cp .env.example .env                     # then set DATABASE_URL; .env is git-ignored, never commit it
alembic upgrade head                     # create/upgrade the schema (works on a fresh database)

python -m scripts.seed_data              # demo agencies + one user per role + one case
python -m scripts.populate_demo_case --scenario trafficking   # plant a demo trafficking ring to discover

uvicorn api.main:app --reload            # http://localhost:8000
```

**Frontend:**
```bash
cd dashboard
npm install
npm run dev                              # http://localhost:5173, proxies /api to :8000
```

## Roles & permissions

Four roles (`schema/user.py`), enforced both in the UI (route-level
gating) and — authoritatively — in the API (`api/auth.require_role` /
`api/permissions.py` per-endpoint authorization checks):

| Role | Scope |
|---|---|
| **Investigator** | View + edit own assigned cases; full dashboard access for those cases |
| **Analyst** | Cross-case, read-only view for pattern spotting — scoped to their own agency, no edit rights |
| **Admin** | Manage user accounts, resolve access requests, and view the audit log — scoped to their own agency |
| **Super Admin** | Everything Admin can do, across **every** agency |

The system's `User` entity (who logs into the software) is kept
architecturally and physically separate from the `Person` entity in the
criminal-network graph (who's a suspect in a case) — mixing the two would
be both a data-integrity bug and a serious privacy problem.

## API surface

Every route needs a logged-in session. Case data is case-scoped: reads
need access to the case, writes (ingest, resolution overrides) need an
**assigned investigator** (or super-admin).

| Route | Purpose |
|---|---|
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/change-password` | Session auth. Login is brute-force protected (per-badge and per-IP lockout via `api/ratelimit.py`, `429` + `Retry-After`); a password change signs out the account's other sessions |
| `POST /cases/`, `GET /cases/`, `GET /cases/{id}` | Create/list/read cases (agency- and role-scoped) |
| `POST /cases/{id}/assign`, `/unassign`, `/status`, `/confidentiality` | Investigator assignment, case status, confidentiality tier (admin) |
| `GET /access-requests/...`, `POST /access-requests/{id}/approve`, `/deny` | Escalating access requests to cases you cannot see |
| `POST /ingest/` | Ingest one document into a case: extraction → resolution → rule-based relations → persisted. CDR/financial documents may carry `structured` rows |
| `GET /ingest/{case_id}/summary` | Per-type document counts |
| `GET /query/{case_id}` | The case graph: resolved nodes with merge confidence, reasons, aliases, member mentions, `needsReview` |
| `GET/POST/DELETE /query/{case_id}/resolution/overrides` | Investigator review: split a wrong merge (`never_merge`) or confirm a missed one (`force_merge`); applied on every rebuild |
| `GET /query/{case_id}/link-predictions` | Likely-but-unrecorded relationships (leads to verify) |
| `GET /evidence/{entity_id}` | Persisted evidence trail (source documents); `403` unless you can read the entity's case |
| `GET /alerts/{case_id}` | Hub-and-spoke, financial-structuring and communication-burst detection on the case's real data |
| `GET /reports/{case_id}`, `POST /reports/{case_id}/generate`, `GET /reports/{case_id}/{report_id}/download` | Markdown / CSV / PDF case reports |
| `GET /ledger/entries`, `GET /ledger/verify` | Hash-chained ledger of evidence-linking events + integrity verification |
| `GET /audit/` | Admin-only audit log (logins, failed logins, account/case-access changes, ingestion, overrides, exports) |
| `GET /users/...` | Admin-only badge-ID lookup, used to resolve a badge ID into the `user_id` that case assignment needs |
| `GET /cases/{id}/cross-case-matches` | Confidentiality-gated matches against cases you cannot see (indexed, not a full pairwise scan) |
| `GET/PUT /settings/` | Per-user dashboard preferences |

## Testing

```bash
pytest tests/ -v                       # the whole suite
pytest tests/ --cov=. --cov-report=term-missing
ruff check .                           # lint (real defects only)
python -m scripts.evaluate --seeds 20  # measured extraction / resolution / relation quality
```

**Verified by running the suite directly: 259 tests, all passing** (schema
validation; the NLP pipeline and the Indian-text rules; transliteration;
multi-signal resolution — including deliberate failure cases such as a
shared surname, a shared family phone, one-digit-off phone numbers, and a
nickname bridging two people; structured CDR/financial parsing; graph and
analytics; the hash-chained ledger; login lockout and the audit log;
migrations against a fresh database; the full API surface including auth,
roles, case scoping, ingest authorization, evidence access control, and the
review workflow; and a planted trafficking ring discovered end-to-end
through the real API). `ruff check .` passes clean.
`tests/test_evaluation_regression.py` guards the quality numbers in
[`docs/EVALUATION.md`](docs/EVALUATION.md) — CI fails the build if they
regress.

## Measured quality

Full methodology, the evaluation sets' limits, and known failure modes are
in [`docs/EVALUATION.md`](docs/EVALUATION.md); headline numbers:

| Stage | Result |
|---|---|
| Extraction (micro-F1) | 0.994 on a 20-document hand-written set; 0.999 on 20 independently generated trafficking rings (1,883 gold mentions) |
| Entity resolution (pairwise P/R) | 1.000 / 1.000 (hand-written set); 1.000 / 0.968 (generated rings); 0 false merges over the generated rings |
| Relations (rule-based, P/R) | 0.978 / 0.978 on the hand-written set (44 of 45 gold relations found) |

These are measured against hand-written and synthetically generated data —
no real police data was used (none can be, in a hackathon) — so treat them
as an upper bound, not an estimate of real-world FIR performance. See the
known-failure-modes section of `docs/EVALUATION.md` for specifics (short
name typos, two people sharing a full name, names outside the gazetteer,
Hindi/regional-script coverage, single-rule pronoun linking).

## Current implementation status

| Stage | Status |
|---|---|
| Entity extraction | Working. spaCy + Indian-FIR rule layer — see [Measured quality](#measured-quality) |
| Entity resolution | Working. Multi-signal, cross-script, surname veto, constraint-aware clustering, review flags, persisted investigator overrides |
| Relations | Working. Deterministic rules by default; zero-shot NLI is an optional extra |
| Structured CDR / financial | Working. Parsed directly into entities and edges; also feeds the anomaly detectors |
| Graph, centrality, communities, link prediction | Working |
| Anomaly detection | Working on real ingested data (hub-and-spoke, financial structuring, communication bursts) |
| Explainability / evidence trail | Working and persisted (`evidence_links`); access-controlled by case; merge reasons shown per node |
| Ledger | Hash-chained; a case reload no longer writes new entries (resolved-entity ids are deterministic) |
| Auth, roles, case scoping | Working; ingest and overrides need assigned-investigator access; login rate-limited; audit log |
| Reports | Markdown, CSV and PDF |
| Migrations | `alembic upgrade head` works on a fresh database and matches the ORM (checked in CI via `alembic check`) |
| Dashboard | Live graph with review markers, evidence panel with merge reasons and one-click split, ingestion form (incl. structured JSON), alerts feed, admin audit log, cross-case matches, ledger integrity view |

## Known limitations

- **Real-world accuracy is unmeasured.** No real police data can be used here; the evaluation sets are
  hand-written and synthetic (see [`docs/EVALUATION.md`](docs/EVALUATION.md) for exactly what that means
  and the known failure modes). Expect lower recall on real FIRs, especially for unusual names and
  regional scripts other than Devanagari.
- Hindi support is rule-based (gazetteer + transliteration), not a statistical model; no
  IndicNER-style checkpoint has had its licence verified (see `THIRD_PARTY_NOTICES.md`).
- Sessions and the login rate limiter are in-process: run **one** API worker (the Dockerfile does)
  or move both to Redis before scaling out. See [`SECURITY.md`](SECURITY.md).
- No encryption-at-rest is configured; durability and encryption depend on the configured database.
- Rule weights in resolution are hand-set, not learned; merge confidence is a ranking aid, not a
  calibrated probability.

## Security

This system is designed to handle real law-enforcement case data. See
[`SECURITY.md`](SECURITY.md) for the reporting process, current security
scope, and known limitations. **Never commit real case data, credentials,
or `.env` files** — `.gitignore` excludes `.env` by default; double-check
before pushing if you've been experimenting locally.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). Chosen over MIT for its
explicit patent grant, given the plausible path to real ministry
deployment or a startup spinoff. Per official SIH rules, project IP stays
with the team; the sponsoring ministry retains free lifetime access. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for the full
dependency license audit (Neo4j Community Edition is deliberately
excluded — GPLv3 conflicts with this permissive-licensed stack).
