"""
scripts/populate_demo_case.py

Bridges data/generate_synthetic.py (which only builds in-memory
SyntheticDocument objects) to the actual database: creates a new Case,
assigns it to an investigator, and runs each generated synthetic
document through the SAME pipeline api/routes/ingestion.py uses
(nlp.pipeline.process_document: extraction -> rule-based relations ->
persist entities and relationships) — not just a bare document insert.

An earlier version of this script only called repo.create_document,
which stores the raw document text but never derives or persists any
entities/relationships from it. That made a freshly seeded case show
up in the dashboard's dropdown but return 501 from /query/{case_id}
("no persisted entities/relationships to build a graph from") —
looking populated while actually being empty. This version mirrors
ingestion.py's real behavior so a seeded case is immediately queryable.

DOC_TYPE_MAP, render_structured_as_text, and ingest_one_document are
deliberately module-level and public (not underscore-prefixed) —
scripts/seed_bulk_cases.py imports and reuses all three directly
rather than re-implementing the same pipeline-bridging logic a second
time. Keep them in sync if either script's needs diverge.

Usage:
    python -m scripts.populate_demo_case
    python -m scripts.populate_demo_case --title "Sector 12 trafficking ring" --firs 5 --cdrs 3 --financial 2
    python -m scripts.populate_demo_case --investigator-badge INV001
    python -m scripts.populate_demo_case --scenario trafficking --title "Border-route trafficking ring"
"""

import argparse
import uuid

from db.connection import SessionLocal, init_db
from db import repository as repo
from data.generate_synthetic import generate_dataset, DocumentType
from nlp.pipeline import process_document
from schema.case import Case
from schema.entities import SourceDocument

# Maps data.generate_synthetic's DocumentType to
# schema.entities.VALID_DOCUMENT_TYPES, since the two modules use
# slightly different naming conventions.
DOC_TYPE_MAP = {
    DocumentType.FIR: "fir",
    DocumentType.CDR: "cdr",
    DocumentType.FINANCIAL_RECORD: "financial",
}


def render_structured_as_text(synth_doc) -> str:
    """Render a CDR/financial SyntheticDocument's structured data as a
    natural-language sentence for extraction to run on.

    Feeding str(synth_doc.structured) (a raw Python dict/list repr)
    straight to extraction was a real bug: dict/list syntax contains
    many phone-number-shaped and digit-shaped substrings that spaCy/
    regex misread as extra phantom entities, which then blew up
    classify_all_relations's O(n^2) pairwise calls into dozens of
    spurious relationship rows for a single document (observed: 45
    relationship inserts from one CDR, enough to time out the DB
    connection mid-transaction). Rendering to plain, minimal prose
    instead means extraction only finds the entities actually present.
    """
    if synth_doc.text:
        return synth_doc.text

    structured = synth_doc.structured

    if "calls" in structured:
        calls = structured["calls"]
        if not calls:
            return "No calls recorded."
        first = calls[0]
        return (
            f"Call detail record: {first['caller']} called {first['callee']} "
            f"{len(calls)} time(s), starting {first['timestamp']}."
        )

    if "transactions" in structured:
        transactions = structured["transactions"]
        if not transactions:
            return "No transactions recorded."
        total = sum(t["amount"] for t in transactions)
        return (
            f"Financial record: account {structured['sender_account']} sent "
            f"{len(transactions)} transaction(s) totaling {total} to account "
            f"{structured['receiver_account']}."
        )

    return "No content recorded."


def ingest_one_document(db, document: SourceDocument) -> dict:
    """Run one document through nlp.pipeline.process_document -- the very
    same function api/routes/ingestion.py calls, so a seeded case and an
    API-ingested case are processed identically (this script used to keep
    its own hand-copied version of that logic, with the O(n^2) zero-shot
    relation classifier and a 12-entity safety cap that silently dropped
    relations for denser documents).

    Degrades gracefully if spaCy or its model is unavailable.
    """
    repo.create_document(db, document)

    try:
        result = process_document(document)
    except RuntimeError as exc:
        return {
            "document_id": document.id,
            "status": "stored_pending_extraction",
            "detail": str(exc),
            "entity_count": 0,
            "relation_count": 0,
        }

    repo.create_entities(db, result.entities)
    repo.create_relationships(db, result.relations)
    return {
        "document_id": document.id,
        "status": "extracted",
        "entity_count": len(result.entities),
        "relation_count": len(result.relations),
        "detail": "; ".join(result.warnings) or None,
    }


def populate(title: str, num_firs: int, num_cdrs: int, num_financial: int,
             investigator_badge_id: str, scenario: str = "random", seed: int = 26189) -> str:
    """Generate a synthetic dataset, create a case for it, run every
    document through the real ingestion pipeline, and assign the given
    investigator to the case.

    Returns:
        The new case's ID.

    Raises:
        ValueError: if investigator_badge_id doesn't match any known user.
    """
    init_db()
    db = SessionLocal()
    try:
        investigator = repo.get_user_by_badge_id(db, investigator_badge_id)
        if investigator is None:
            raise ValueError(
                f"No user found with badge_id={investigator_badge_id!r}. "
                f"Run scripts/seed_data.py first, or pass --investigator-badge "
                f"with a real badge id."
            )

        case = Case(
            id=str(uuid.uuid4()),
            title=title,
            agency_id=investigator.agency_id,
            created_by_user_id=investigator.id,
        )
        created_case = repo.create_case(db, case)
        repo.assign_investigator(db, created_case.id, investigator.id)

        if scenario == "trafficking":
            # Structured, fictional trafficking ring (see
            # data/trafficking_scenario.py): recruitment funnel, transporter,
            # safehouse behind a front organisation, sub-threshold payments.
            from data.trafficking_scenario import generate_trafficking_ring
            dataset = generate_trafficking_ring(seed=seed, num_firs=num_firs).documents
        else:
            dataset = generate_dataset(
                num_firs=num_firs, num_cdrs=num_cdrs, num_financial=num_financial,
            )

        total_entities = 0
        total_relations = 0
        extraction_unavailable = False

        for synth_doc in dataset:
            # FIRs carry their content in .text; CDR/financial records
            # carry it in .structured. See render_structured_as_text
            # for why this can't just be str(synth_doc.structured) —
            # that fed dict/list repr syntax to extraction and produced
            # dozens of spurious phantom entities.
            raw_text = render_structured_as_text(synth_doc)

            document = SourceDocument(
                id=synth_doc.doc_id,
                document_type=DOC_TYPE_MAP[synth_doc.doc_type],
                raw_text=raw_text,
                case_id=created_case.id,
                # CDR/financial docs carry their real detection-relevant
                # data (call timestamps, transaction amounts) here, not
                # in raw_text — see schema.entities.SourceDocument's
                # docstring. Without this, detect_anomalies's
                # structuring/burst checks silently have nothing to
                # scan even for seeded demo data.
                structured=synth_doc.structured or {},
            )
            result = ingest_one_document(db, document)

            total_entities += result["entity_count"]
            total_relations += result["relation_count"]
            if result["status"] == "stored_pending_extraction":
                extraction_unavailable = True

        print(f"Created case {created_case.id!r} ({title!r}) in agency "
              f"{investigator.agency_id!r}, assigned to {investigator.name} "
              f"({investigator_badge_id}).")
        print(f"Ingested {len(dataset)} synthetic documents "
              f"(scenario: {scenario}).")
        print(f"Extracted {total_entities} entities, "
              f"{total_relations} relations across all documents.")

        if extraction_unavailable:
            print("WARNING: spaCy (or its model) was unavailable for at least "
                  "one document — some documents were stored but not extracted. "
                  "Run: python -m spacy download en_core_web_sm")

        if total_entities > 0:
            print(f"Log in as {investigator.name} ({investigator_badge_id}) — "
                  f"the case should now appear in the dashboard with a real, "
                  f"queryable graph (not just an entry in the dropdown).")
        else:
            print("WARNING: zero entities were extracted from any document — "
                  "this case will still show 501/empty when queried. Check "
                  "that spaCy and en_core_web_sm are installed.")

        return created_case.id
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic data and populate a real, queryable case with it."
    )
    parser.add_argument("--title", default="Synthetic demo case",
                         help="Title for the new case.")
    parser.add_argument("--firs", type=int, default=5, dest="num_firs",
                         help="Number of synthetic FIRs to generate.")
    parser.add_argument("--cdrs", type=int, default=3, dest="num_cdrs",
                         help="Number of synthetic CDRs to generate.")
    parser.add_argument("--financial", type=int, default=2, dest="num_financial",
                         help="Number of synthetic financial records to generate.")
    parser.add_argument("--investigator-badge", default="INV001",
                         dest="investigator_badge_id",
                         help="Badge ID of the investigator to assign this case to "
                              "(default: INV001, the seed_data.py demo investigator).")
    parser.add_argument("--scenario", choices=["random", "trafficking"], default="random",
                         help="'trafficking' plants a structured trafficking ring with a hub-and-spoke "
                              "recruitment funnel, aliases, several SIMs and structured payments "
                              "(the Women Safety Division scenario). Default: random independent records.")
    parser.add_argument("--seed", type=int, default=26189, help="Seed for --scenario trafficking.")
    args = parser.parse_args()

    populate(
        title=args.title,
        num_firs=args.num_firs,
        num_cdrs=args.num_cdrs,
        num_financial=args.num_financial,
        investigator_badge_id=args.investigator_badge_id,
        scenario=args.scenario,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
