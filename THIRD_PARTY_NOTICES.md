# Third-Party Notices — SUTRA (SIH26189, AI-Powered Criminal Network Analysis System)

This project is licensed under **Apache License 2.0** (see `LICENSE`).

Per official SIH rules, project IP stays with the student team, but the team
is solely responsible for any plagiarism, IP, or copyright violations arising
from third-party components used in the solution. This file exists to keep
that check auditable — update it whenever a new dependency is added.

## Dependency License Summary

| Component | Used for | License | Compatible w/ Apache 2.0? | Notes |
|---|---|---|---|---|
| spaCy | NLP entity extraction | MIT | ✅ Yes | |
| HuggingFace Transformers | NLP entity extraction | Apache 2.0 | ✅ Yes | Same license family |
| IndicNER (or similar) | Indian name/script NER | Check per-model license on HuggingFace Hub before use | ⚠️ Verify | Model licenses vary by publisher — confirm before shipping |
| RapidFuzz | Entity resolution (fuzzy match) | MIT | ✅ Yes | |
| scikit-learn | Confidence scoring / clustering | BSD-3-Clause | ✅ Yes | |
| NetworkX | Graph construction & analytics | BSD-3-Clause | ✅ Yes | **Recommended over Neo4j Community** — no copyleft concerns |
| Neo4j Community Edition | Graph DB (alternative) | GPLv3 | ⚠️ Caution | Copyleft license — stricter redistribution terms than the rest of the stack. Avoid unless graph-DB-scale features are specifically required. |
| Neo4j Graph Data Science library | Graph algorithms (if using Neo4j) | Depends on edition — verify | ⚠️ Verify | Only relevant if Neo4j is used |
| Flask | Backend API | BSD-3-Clause | ✅ Yes | |
| FastAPI | Backend API (alternative) | MIT | ✅ Yes | |
| PostgreSQL | Evidence-trail storage | PostgreSQL License (permissive) | ✅ Yes | |
| SQLite | Evidence-trail storage (lighter option) | Public Domain | ✅ Yes | |
| React | Dashboard frontend | MIT | ✅ Yes | |
| Cytoscape.js | Graph visualization UI | MIT | ✅ Yes | |
| D3.js | Graph visualization UI (alternative) | ISC | ✅ Yes | |
| Tailwind CSS | Dashboard styling | MIT | ✅ Yes | |
| Faker (Python) | Synthetic data generation | MIT | ✅ Yes | |

## Action items before December submission

1. **Confirm NetworkX over Neo4j** as the default graph backend, per Section 10
   of the project notes — this removes the only GPLv3 dependency from the stack.
2. **Verify the exact IndicNER model license** on HuggingFace before bundling —
   NER model weights sometimes carry separate licenses from the library code.
3. If any pretrained model card lists "non-commercial use only" or similar
   restrictions, do not use it — SIH solutions may be adopted/deployed by the
   ministry, so non-commercial-only models create a real conflict.
4. Re-run this check any time a new library is added to `requirements.txt` or
   `package.json`.

## Data disclaimer

All FIR, CDR, financial, and criminal-history records used in this project are
**entirely synthetic and fictional**. No real personal data, real case data,
or real individuals are represented. See project notes Section 9 for the
synthetic data generation strategy.

## Additions

| Component | Licence | Notes |
|---|---|---|
| reportlab | BSD-3-Clause | PDF report export (`api/report_builder.py`) |
| Alembic | MIT | Schema migrations |
| pytest-cov, ruff | MIT | Dev/CI only |
| torch, transformers | BSD-3-Clause / Apache-2.0 | **Optional** (`requirements-ml.txt`); not needed by the default pipeline. Verify the licence of any *model weights* you download separately: they are often not under the library's licence. |

The Devanagari/Hindi support is rule-based (gazetteer + transliteration written for this project); it
adopts no third-party model, so no additional model licence applies.
