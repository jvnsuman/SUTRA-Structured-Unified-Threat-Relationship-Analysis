"""
scripts/seed_bulk_cases.py

Creates a batch of ~25 real, queryable cases (through the exact same
ingestion pipeline api/routes/ingestion.py and
scripts/populate_demo_case.py use — extraction, persisted entities,
relation classification, persisted relationships), spread across
scripts/seed_data.py's three demo investigators (INV001/AG-NCRB,
INV002/AG-NCRB, INV003/AG-WSD) — plus applies graph.analytics's full
algorithm suite (centrality, community detection, anomaly detection,
link prediction) to every case and prints a one-line summary of what
each found, so a demo can point at concrete numbers rather than an
empty dropdown with 25 more names in it.

Deliberate cross-case entity overlap: a handful of these cases share a
planted phone number or person name with another case IN A DIFFERENT
AGENCY (via data.generate_synthetic's suspect_name/suspect_phone/
caller_phone override parameters — see that module for why they
exist). This is what gives api/routes/cross_case.py's match panel and
the resulting access-request flow (api/routes/access_requests.py)
something real to find when demoed — without a planted overlap, 25
independently-random cases would almost certainly never happen to
share an entity, and the cross-case feature would look unused rather
than merely untriggered.

Idempotent-ish: re-running this creates 25 MORE cases (it does not
check for or skip previously seeded ones) — it's a generator script
for demo data, not a migration. Run scripts/seed_data.py first so the
three investigators/agencies it depends on already exist.

Usage:
    python -m scripts.seed_bulk_cases
    python -m scripts.seed_bulk_cases --count 25
"""

import argparse
import uuid

import graph.analytics as graph_analytics
import graph.build as graph_build
from api.casegraph import build_case_graph
from data.generate_synthetic import (
    DocumentType,
    generate_dataset,
    generate_synthetic_cdr,
    generate_synthetic_fir,
)
from db import repository as repo
from db.connection import SessionLocal, init_db
from ledger.chain import LedgerEventType
from schema.case import Case
from schema.entities import SourceDocument
from scripts.populate_demo_case import DOC_TYPE_MAP, ingest_one_document, render_structured_as_text

# The three investigators from scripts/seed_data.py's demo_users list.
# Cases are round-robined across these so the batch has real variety
# across both agencies rather than piling onto one investigator.
_INVESTIGATOR_BADGES = ["INV001", "INV002", "INV003"]

# Case title templates, varied enough that 25 auto-generated cases
# don't all look identical in the case selector dropdown.
_CASE_TITLE_TEMPLATES = [
    "Trafficking network — {location} corridor",
    "Financial fraud ring — {location} branch",
    "Organized theft — {location} district",
    "Extortion network — {location} sector",
    "Narcotics distribution — {location} route",
    "Cybercrime cell — {location} operations",
    "Human smuggling — {location} crossing",
    "Counterfeit currency ring — {location} market",
]

_LOCATIONS_FOR_TITLES = [
    "Sector 9", "Sector 12", "MG Road", "Old City", "Riverside",
    "Industrial Zone", "Central", "North Ring", "Station Area", "Cantonment",
]


def _build_case_title(index: int) -> str:
    """A varied, non-repeating-looking title for case number `index`."""
    template = _CASE_TITLE_TEMPLATES[index % len(_CASE_TITLE_TEMPLATES)]
    location = _LOCATIONS_FOR_TITLES[index % len(_LOCATIONS_FOR_TITLES)]
    return template.format(location=location)


def _ingest_synthetic_documents(db, case_id: str, synthetic_docs: list) -> tuple:
    """Run a list of already-built SyntheticDocument objects through
    the real pipeline (same ingest_one_document scripts/
    populate_demo_case.py uses) and return (total_entities,
    total_relations).
    """
    total_entities = 0
    total_relations = 0
    for synth_doc in synthetic_docs:
        raw_text = render_structured_as_text(synth_doc)
        document = SourceDocument(
            id=synth_doc.doc_id,
            document_type=DOC_TYPE_MAP[synth_doc.doc_type],
            raw_text=raw_text,
            case_id=case_id,
            structured=synth_doc.structured or {},
        )
        result = ingest_one_document(db, document)
        total_entities += result["entity_count"]
        total_relations += result["relation_count"]
    return total_entities, total_relations


def _create_case_with_documents(db, title: str, investigator_badge_id: str, synthetic_docs: list) -> str:
    """Create one case, assign it to the given investigator, ingest
    the given pre-built synthetic documents into it, and append a
    CASE_CREATED ledger entry (same event api/routes/cases.py's
    POST /cases/ appends — this script bypasses that HTTP endpoint
    for bulk-seeding speed, but the ledger should still record case
    creation as a real event either way, not just for API-created
    cases).

    Returns the new case's ID.
    """
    investigator = repo.get_user_by_badge_id(db, investigator_badge_id)
    if investigator is None:
        raise ValueError(
            f"No user found with badge_id={investigator_badge_id!r}. "
            f"Run scripts/seed_data.py first."
        )

    case = Case(
        id=str(uuid.uuid4()),
        title=title,
        agency_id=investigator.agency_id,
        description=f"Synthetic demo case seeded for {investigator.agency_id}.",
        created_by_user_id=investigator.id,
    )
    created_case = repo.create_case(db, case)
    repo.assign_investigator(db, created_case.id, investigator.id)
    repo.append_ledger_entry(
        db,
        LedgerEventType.CASE_CREATED,
        {"case_id": created_case.id, "title": created_case.title, "agency_id": created_case.agency_id, "created_by_user_id": investigator.id},
    )

    _ingest_synthetic_documents(db, created_case.id, synthetic_docs)
    return created_case.id


def _print_case_analytics_summary(db, case_id: str, title: str) -> None:
    """Build the case's graph and run every graph.analytics algorithm
    against it, printing a one-line-per-algorithm summary. Best-effort:
    prints a skip note rather than raising if the case ends up with
    too few entities/edges for a given algorithm to say anything
    (e.g. link prediction needs at least 2 nodes; community detection
    is uninteresting on a single connected pair).
    """
    graph, _resolved, _mentions = build_case_graph(db, case_id)
    if graph is None:
        print(f"  [{title}] no entities extracted — skipping analytics (spaCy unavailable?)", flush=True)
        return

    influencer = graph_build.highlight_influencer(graph)
    influencer_note = influencer["reason"] if influencer else "no influencer (empty graph)"

    communities = graph_analytics.compute_community_detection(graph)
    community_count = len(set(communities.values())) if communities else 0

    anomalies = graph_analytics.detect_anomalies(graph)

    predictions = graph_analytics.compute_link_predictions(graph, top_n=3)

    print(
        f"  [{title}] {graph.number_of_nodes()} entities, {graph.number_of_edges()} relationships | "
        f"{influencer_note} | {community_count} communities detected | "
        f"{len(anomalies)} anomalies flagged | {len(predictions)} link predictions",
        flush=True,
    )


def seed_bulk_cases(count: int = 25) -> list:
    """Create `count` real, queryable cases spread across the three
    demo investigators, with a handful of deliberately planted
    cross-agency entity overlaps (see module docstring), printing
    progress as each case is created and analyzed.

    Each case runs its documents through real spaCy NER + HuggingFace
    zero-shot relation classification (see project overview) — this
    is genuinely slow (multiple seconds per document, not per case),
    so progress is printed PER CASE as it completes, not batched at
    the end. A silent multi-minute run with no output is the actual
    problem this fixes: earlier versions of this script printed
    nothing until every case was created AND analyzed, which is
    indistinguishable from a hang for a run that legitimately takes
    several minutes.

    Returns:
        The list of created case IDs.
    """
    init_db()
    db = SessionLocal()
    created_case_ids = []

    def _create_and_report(title: str, investigator_badge_id: str, synthetic_docs: list, case_number: int, total: int) -> str:
        """Create one case, ingest its documents, print its analytics
        summary immediately, and return its ID — the per-case unit of
        work this whole function just repeats, with progress printed
        at each step rather than saved up for the end.
        """
        print(f"[{case_number}/{total}] Creating \"{title}\" ({investigator_badge_id})…", flush=True)
        case_id = _create_case_with_documents(db, title, investigator_badge_id, synthetic_docs)
        _print_case_analytics_summary(db, case_id, title)
        return case_id

    try:
        # --- Planted cross-agency overlaps (created first, in pairs) ---
        # Each pair shares EITHER a suspect name OR a phone number
        # across one AG-NCRB case and one AG-WSD case, so a viewer in
        # one agency who queries their case's cross-case-matches panel
        # (api/routes/cross_case.py) will find a real match pointing
        # at a case in the other agency — the exact scenario the
        # access-request flow (api/routes/access_requests.py) exists
        # to demonstrate.
        overlap_plans = [
            {"shared_name": "Suresh Pillai"},
            {"shared_phone": "998-112-3344"},
            {"shared_name": "Meena Kapoor"},
        ]
        # Each plan creates 2 cases (one per agency in the pair), so
        # cap how many plans run if `count` is small enough that even
        # the planted-overlap pairs alone would exceed it — count is a
        # hard ceiling on total cases created, not a floor the overlap
        # pairs are exempt from.
        max_plans_for_count = max(0, count // 2)
        overlap_plans = overlap_plans[:max_plans_for_count]

        overlap_case_count = 0
        for plan_index, plan in enumerate(overlap_plans):
            shared_name = plan.get("shared_name")
            shared_phone = plan.get("shared_phone")

            # First case of the pair: AG-NCRB investigator.
            docs_a = [
                generate_synthetic_fir(suspect_name=shared_name, suspect_phone=shared_phone),
                generate_synthetic_fir(),
            ]
            if shared_phone:
                docs_a.append(generate_synthetic_cdr(caller_phone=shared_phone))

            title_a = f"{_build_case_title(overlap_case_count)} (planted overlap {plan_index + 1}a)"
            case_id_a = _create_and_report(title_a, "INV001", docs_a, len(created_case_ids) + 1, count)
            created_case_ids.append(case_id_a)
            overlap_case_count += 1

            # Second case of the pair: AG-WSD investigator — same
            # shared_name/shared_phone value, different everything else.
            docs_b = [
                generate_synthetic_fir(suspect_name=shared_name, suspect_phone=shared_phone),
                generate_synthetic_fir(),
            ]
            if shared_phone:
                docs_b.append(generate_synthetic_cdr(caller_phone=shared_phone))

            title_b = f"{_build_case_title(overlap_case_count)} (planted overlap {plan_index + 1}b)"
            case_id_b = _create_and_report(title_b, "INV003", docs_b, len(created_case_ids) + 1, count)
            created_case_ids.append(case_id_b)
            overlap_case_count += 1

        # --- Remaining independent cases, round-robined across all 3 investigators ---
        remaining = max(0, count - len(created_case_ids))
        for i in range(remaining):
            case_index = len(created_case_ids)
            title = _build_case_title(case_index)
            investigator_badge_id = _INVESTIGATOR_BADGES[case_index % len(_INVESTIGATOR_BADGES)]

            dataset = generate_dataset(num_firs=4, num_cdrs=2, num_financial=1)
            case_id = _create_and_report(title, investigator_badge_id, dataset, len(created_case_ids) + 1, count)
            created_case_ids.append(case_id)

        print(f"\nDone. Seeded {len(created_case_ids)} cases "
              f"({len(overlap_plans) * 2} with planted cross-agency overlaps, "
              f"{len(created_case_ids) - len(overlap_plans) * 2} independent).", flush=True)

        return created_case_ids
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Seed ~25 real, queryable cases with planted cross-agency entity overlaps."
    )
    parser.add_argument("--count", type=int, default=25, help="Total number of cases to create (default: 25).")
    args = parser.parse_args()
    seed_bulk_cases(count=args.count)


if __name__ == "__main__":
    main()
