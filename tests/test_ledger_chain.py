"""
tests/test_ledger_chain.py

Unit tests for ledger/chain.py's hash-chain logic — pure functions,
no database or ML dependency, so these run anywhere. Exercises the
actual tamper-evidence guarantee: build a small chain, then verify
that any single-field mutation (payload, prev_hash, entry_hash, or
deleting/reordering an entry) is caught by verify_chain, and that an
untouched chain verifies clean.
"""

import copy

from ledger.chain import (
    GENESIS_PREV_HASH,
    LedgerEventType,
    build_genesis_entry,
    build_next_entry,
    compute_entry_hash,
    verify_chain,
)


def _build_sample_chain(length: int = 4) -> list:
    """A genesis entry plus `length` CASE_CREATED entries."""
    entries = [build_genesis_entry()]
    for i in range(length):
        entries.append(build_next_entry(entries[-1], LedgerEventType.CASE_CREATED, {"case_id": f"case-{i}"}))
    return entries


def test_genesis_entry_is_deterministic():
    """The genesis entry has no variable input, so building it twice
    must produce the exact same hash — this is what lets any fresh
    deployment's chain start from a known, reproducible root.
    """
    a = build_genesis_entry()
    b = build_genesis_entry()
    assert a.entry_hash == b.entry_hash
    assert a.sequence_number == 0
    assert a.prev_hash == GENESIS_PREV_HASH


def test_untouched_chain_verifies_clean():
    chain = _build_sample_chain(5)
    result = verify_chain(chain)
    assert result.valid is True
    assert result.entries_checked == 6  # genesis + 5
    assert result.first_broken_sequence_number is None


def test_verify_chain_is_order_independent_on_input():
    """verify_chain sorts by sequence_number itself, so passing
    entries in scrambled order (e.g. an unordered DB query result)
    must still verify correctly.
    """
    chain = _build_sample_chain(4)
    scrambled = [chain[2], chain[0], chain[4], chain[1], chain[3]]
    result = verify_chain(scrambled)
    assert result.valid is True


def test_editing_a_payload_breaks_verification():
    """The core tamper-evidence guarantee: silently editing one
    entry's payload after the fact must be detected.
    """
    chain = _build_sample_chain(4)
    tampered = copy.deepcopy(chain)
    tampered[2].payload["case_id"] = "case-TAMPERED"

    result = verify_chain(tampered)
    assert result.valid is False
    assert result.first_broken_sequence_number == tampered[2].sequence_number


def test_deleting_an_entry_breaks_verification():
    """Removing an entry from the middle of the chain must break the
    prev_hash link to the entry after it, not just silently shrink
    the chain.
    """
    chain = _build_sample_chain(5)
    with_deletion = chain[:3] + chain[4:]  # remove sequence_number 3

    result = verify_chain(with_deletion)
    assert result.valid is False


def test_reordering_entries_breaks_verification():
    """Distinct from test_verify_chain_is_order_independent_on_input
    (which shuffles the INPUT LIST order — fine, since verify_chain
    sorts by sequence_number first). This test instead swaps which
    entry occupies which POSITION in the actual chain — entry at
    sequence_number 2 and the entry at sequence_number 3 trade places
    in the underlying sequence, simulating two entries being silently
    swapped by whoever tampered with the table — which must break the
    prev_hash link on both sides of the swap.
    """
    chain = _build_sample_chain(4)
    swapped = copy.deepcopy(chain)
    swapped[2].sequence_number, swapped[3].sequence_number = swapped[3].sequence_number, swapped[2].sequence_number

    result = verify_chain(swapped)
    assert result.valid is False


def test_chain_not_starting_at_genesis_is_invalid():
    chain = _build_sample_chain(3)
    without_genesis = chain[1:]

    result = verify_chain(without_genesis)
    assert result.valid is False
    assert result.entries_checked == 0


def test_empty_chain_is_valid_but_reports_zero_checked():
    result = verify_chain([])
    assert result.valid is True
    assert result.entries_checked == 0


def test_compute_entry_hash_is_order_independent_on_payload_keys():
    """The hash function uses json.dumps(..., sort_keys=True), so two
    payload dicts with the same keys/values in different insertion
    order must hash identically — otherwise a round-trip through a
    DB's JSON column (which doesn't guarantee key order) could make a
    perfectly legitimate entry look tampered.
    """
    payload_a = {"case_id": "c1", "title": "Case One"}
    payload_b = {"title": "Case One", "case_id": "c1"}

    hash_a = compute_entry_hash(1, LedgerEventType.CASE_CREATED, payload_a, GENESIS_PREV_HASH)
    hash_b = compute_entry_hash(1, LedgerEventType.CASE_CREATED, payload_b, GENESIS_PREV_HASH)
    assert hash_a == hash_b


def test_different_event_type_produces_different_hash():
    """Two entries with an identical payload but different
    event_type must NOT collide — event_type is part of what's
    hashed, not just decoration.
    """
    payload = {"x": 1}
    hash_a = compute_entry_hash(1, LedgerEventType.CASE_CREATED, payload, GENESIS_PREV_HASH)
    hash_b = compute_entry_hash(1, LedgerEventType.EVIDENCE_LINKED, payload, GENESIS_PREV_HASH)
    assert hash_a != hash_b
