"""
scripts/test_everything.py

A comprehensive, real-environment smoke test for everything built in
this session: the hash-chain ledger, its concurrency fix, link
prediction, and the cross-case/access-request feature — run against
YOUR real database (whatever DATABASE_URL is set to), not a
throwaway one.

This is deliberately NOT a pytest file — it's a standalone script you
run directly so you get clear pass/fail output without needing to
untangle conftest.py fixtures or pytest configuration. It prints a
summary at the end and exits non-zero if anything failed.

WARNING: This creates real rows in your database (a handful of test
cases, ledger entries, an access request). It does not delete
anything that already exists, and it does not clean up after itself
— by design, so you can inspect what it created afterward. Safe to
run against your dev/demo database; do NOT point it at a database you
can't afford to add test data to.

Usage:
    python -m scripts.test_everything
"""

import sys
import threading
import time
import traceback
import uuid

results = []  # (name, passed: bool, detail: str)


def check(name):
    """Decorator: run a test function, catch anything it raises, and
    record a pass/fail result rather than letting one failing test
    stop the whole script.
    """
    def decorator(fn):
        print(f"\n--- {name} ---")
        try:
            fn()
            results.append((name, True, ""))
            print(f"[PASS] {name}")
        except Exception as e:
            results.append((name, False, f"{type(e).__name__}: {e}"))
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
        return fn
    return decorator


def main():
    from db.connection import SessionLocal, init_db
    from db import repository as repo
    from ledger.chain import LedgerEventType, verify_chain
    from schema.case import Case, CaseConfidentiality
    from schema.access_request import new_access_request

    init_db()

    # =========================================================
    # 1. LEDGER — basic append + verify against your real DB
    # =========================================================
    @check("1. Ledger: genesis entry exists at sequence_number=0")
    def _():
        db = SessionLocal()
        try:
            # ensure_genesis_entry returns the CURRENT TIP if the
            # ledger is already seeded (see its own docstring: "does
            # nothing if a genesis entry already exists") — it does
            # NOT necessarily return the genesis entry itself once
            # other entries exist. The real thing to check is that a
            # genesis entry exists SOMEWHERE at sequence_number=0,
            # which is what actually matters for verify_chain.
            repo.ensure_genesis_entry(db)  # safe to call regardless; seeds if empty
            all_entries = repo.get_all_ledger_entries(db)
            assert len(all_entries) > 0, "Ledger is completely empty after ensure_genesis_entry"
            genesis_candidates = [e for e in all_entries if e.sequence_number == 0]
            assert len(genesis_candidates) == 1, f"Expected exactly one entry at sequence_number=0, found {len(genesis_candidates)}"
            assert genesis_candidates[0].event_type.value == "genesis"
        finally:
            db.close()

    @check("2. Ledger: append a real entry and verify the chain")
    def _():
        db = SessionLocal()
        try:
            before = repo.get_all_ledger_entries(db)
            before_count = len(before)

            new_entry = repo.append_ledger_entry(
                db, LedgerEventType.CASE_CREATED,
                {"case_id": "test-marker", "title": "TEST — safe to ignore/delete", "agency_id": "AG-NCRB"},
            )
            assert new_entry.sequence_number == before_count

            after = repo.get_all_ledger_entries(db)
            assert len(after) == before_count + 1

            result = verify_chain(after)
            assert result.valid, f"Chain broken: {result.detail}"
        finally:
            db.close()

    # =========================================================
    # 2. LEDGER — the actual concurrency bug you hit, reproduced
    #    against YOUR real database (not SQLite)
    # =========================================================
    @check("3. Ledger: concurrent appends do not crash (the bug you saw)")
    def _():
        errors = []

        def worker(n):
            db = SessionLocal()
            try:
                for i in range(10):
                    repo.append_ledger_entry(
                        db, LedgerEventType.CASE_CREATED,
                        {"case_id": f"test-race-{n}-{i}", "title": "TEST race", "agency_id": "AG-NCRB"},
                    )
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}")
            finally:
                db.close()

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent appends raised errors (the bug is NOT fixed): {errors}"

        db = SessionLocal()
        try:
            entries = repo.get_all_ledger_entries(db)
            result = verify_chain(entries)
            assert result.valid, f"Chain broken after concurrent writes: {result.detail}"
        finally:
            db.close()

    # =========================================================
    # 3. CASE + CONFIDENTIALITY
    # =========================================================
    test_case_id = str(uuid.uuid4())

    @check("4. Case: create with description and confidentiality")
    def _():
        db = SessionLocal()
        try:
            case = Case(
                id=test_case_id,
                title="TEST — safe to ignore/delete",
                agency_id="AG-NCRB",
                description="Created by scripts/test_everything.py",
            )
            created = repo.create_case(db, case)
            assert created.description == "Created by scripts/test_everything.py"
            assert created.confidentiality == CaseConfidentiality.NORMAL

            updated = repo.set_case_confidentiality(db, test_case_id, CaseConfidentiality.RESTRICTED)
            assert updated.confidentiality == CaseConfidentiality.RESTRICTED
        finally:
            db.close()

    # =========================================================
    # 4. LINK PREDICTION — real algorithm, real graph
    # =========================================================
    @check("5. Link prediction: Adamic-Adar produces correct ranking")
    def _():
        import networkx as nx
        from graph.analytics import compute_link_predictions

        g = nx.MultiDiGraph()
        for n in ["A", "B", "C", "D", "E"]:
            g.add_node(n)
        g.add_edge("A", "C", relation_type="calls")
        g.add_edge("B", "C", relation_type="calls")
        g.add_edge("A", "D", relation_type="transacts-with")
        g.add_edge("B", "D", relation_type="transacts-with")
        g.add_edge("E", "A", relation_type="calls")

        predictions = compute_link_predictions(g, top_n=5)
        assert len(predictions) > 0, "compute_link_predictions returned nothing (regression!)"
        top = predictions[0]
        assert {top["entity_a_id"], top["entity_b_id"]} == {"A", "B"}, \
            f"Expected A-B to rank highest, got {top}"

    # =========================================================
    # 5. ACCESS REQUEST lifecycle
    # =========================================================
    @check("6. Access request: create, deny (escalates), approve")
    def _():
        db = SessionLocal()
        try:
            other_case_id = str(uuid.uuid4())
            repo.create_case(
                db, Case(id=other_case_id, title="TEST target case", agency_id="AG-WSD",
                         description="Created by scripts/test_everything.py"),
            )

            investigator = repo.get_user_by_badge_id(db, "INV001")
            assert investigator is not None, "INV001 not found — run scripts/seed_data.py first"

            access_request = new_access_request(
                requester_user_id=investigator.id,
                target_case_id=other_case_id,
                matched_entity_id="test-entity",
                reason="Automated test",
            )
            created = repo.create_access_request(db, access_request)
            assert created.status.value == "pending_investigator"

            denied = repo.deny_access_request(db, created.id, denied_by_user_id=investigator.id, note="test deny")
            assert denied.status.value == "pending_admin", "Deny should escalate, not finalize"

            admin = repo.get_user_by_badge_id(db, "ADM001")
            resolved = repo.resolve_access_request(db, created.id, approve=True, resolved_by_user_id=admin.id)
            assert resolved.status.value == "approved"
        finally:
            db.close()

    # =========================================================
    # SUMMARY
    # =========================================================
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    for name, ok, detail in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    print(f"\n{passed}/{len(results)} passed, {failed} failed.")

    if failed:
        print("\nSome checks failed — see [FAIL] lines and tracebacks above.")
        sys.exit(1)
    else:
        print("\nAll checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()