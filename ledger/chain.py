"""
ledger/chain.py

A hash-chained, append-only integrity ledger — the "blockchain" half
of this project's Blockchain & Cybersecurity theme. This is
deliberately NOT a distributed/multi-node blockchain (there is no
consensus protocol and no peer network, since a single-agency
investigative platform has no legitimate multi-party trust problem to
solve with one) — it is the specific blockchain PATTERN that actually
matters for evidence integrity: every event is hashed together with
the previous event's hash, so altering or deleting any past entry
breaks every hash after it. That break is cheaply and conclusively
detectable by verify_chain(), which is the actual guarantee an
investigator or prosecutor cares about ("has this evidence record
been tampered with since it was created?"), not the presence of a
peer-to-peer network.

Recorded event types (see LedgerEventType): case creation, evidence
linked to an entity/pattern (graph.explainability.link_evidence),
and access-request resolution (api/routes/access_requests.py) — the
three places in this system where "what happened, and in what order"
matters enough to be worth a tamper-evident record. This is
additive: nothing here replaces the normal DB rows for cases,
entities, or access requests — the ledger is a parallel, append-only
audit trail of EVENTS, not the primary store for any of that data.

Genesis entry: sequence_number=0, prev_hash="0"*64, a fixed sentinel
so verify_chain() has a defined starting point even on an empty
ledger. Created once by ensure_genesis_entry() (idempotent — safe to
call on every app startup).
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

GENESIS_PREV_HASH = "0" * 64


class LedgerEventType(str, Enum):
    """What kind of event a ledger entry records."""

    GENESIS = "genesis"
    CASE_CREATED = "case_created"
    EVIDENCE_LINKED = "evidence_linked"
    ACCESS_REQUEST_RESOLVED = "access_request_resolved"


@dataclass
class LedgerEntry:
    """One append-only, hash-chained ledger entry.

    entry_hash = SHA-256(sequence_number || event_type || payload_json
    || prev_hash), where payload_json is json.dumps(payload,
    sort_keys=True) — sort_keys is required so the exact same payload
    dict always serializes identically regardless of insertion order,
    which is what makes entry_hash reproducible by verify_chain().
    """

    id: str
    sequence_number: int
    event_type: LedgerEventType
    payload: dict
    prev_hash: str
    entry_hash: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sequence_number": self.sequence_number,
            "event_type": self.event_type.value,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
            "created_at": self.created_at,
        }


def compute_entry_hash(sequence_number: int, event_type: LedgerEventType, payload: dict, prev_hash: str) -> str:
    """The one canonical hash function every entry is built and
    re-verified with — a single source of truth so append_entry (at
    write time) and verify_chain (at read time) can never silently
    drift into computing the hash two different ways.
    """
    payload_json = json.dumps(payload, sort_keys=True)
    material = f"{sequence_number}|{event_type.value}|{payload_json}|{prev_hash}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_genesis_entry() -> LedgerEntry:
    """The fixed, deterministic first entry of every ledger — same
    hash every time, since it has no variable payload. Used by
    db.repository.ensure_genesis_entry to seed an empty ledger table.
    """
    payload = {"message": "Genesis entry — ledger initialized."}
    entry_hash = compute_entry_hash(0, LedgerEventType.GENESIS, payload, GENESIS_PREV_HASH)
    return LedgerEntry(
        id=str(uuid.uuid4()),
        sequence_number=0,
        event_type=LedgerEventType.GENESIS,
        payload=payload,
        prev_hash=GENESIS_PREV_HASH,
        entry_hash=entry_hash,
    )


def build_next_entry(previous_entry: LedgerEntry, event_type: LedgerEventType, payload: dict) -> LedgerEntry:
    """Construct the next entry in the chain, given the current last
    entry. Pure/no I/O — db.repository.append_ledger_entry is what
    actually reads the current last entry, calls this, and persists
    the result; kept separate so the hashing logic itself has no
    database dependency and can be unit-tested in isolation.
    """
    sequence_number = previous_entry.sequence_number + 1
    entry_hash = compute_entry_hash(sequence_number, event_type, payload, previous_entry.entry_hash)
    return LedgerEntry(
        id=str(uuid.uuid4()),
        sequence_number=sequence_number,
        event_type=event_type,
        payload=payload,
        prev_hash=previous_entry.entry_hash,
        entry_hash=entry_hash,
    )


@dataclass
class VerificationResult:
    """Result of walking and re-hashing the entire chain."""

    valid: bool
    entries_checked: int
    first_broken_sequence_number: Optional[int] = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "entries_checked": self.entries_checked,
            "first_broken_sequence_number": self.first_broken_sequence_number,
            "detail": self.detail,
        }


def verify_chain(entries: list) -> VerificationResult:
    """Walk the whole chain in sequence order and recompute every
    entry's hash from its own (sequence_number, event_type, payload,
    prev_hash) — the actual tamper-evidence check. Returns valid=True
    only if every recomputed hash matches the stored entry_hash AND
    every entry's prev_hash matches the PREVIOUS entry's entry_hash
    (this second check is what catches a deleted or reordered entry,
    not just an edited payload).

    Args:
        entries: LedgerEntry objects, any order — sorted here by
            sequence_number before checking, so callers don't need to
            pre-sort (e.g. a DB query without an ORDER BY).

    Returns:
        VerificationResult. On the first mismatch found, stops and
        reports that entry's sequence_number rather than continuing
        to check (and possibly cascading false positives) past a
        known break.
    """
    ordered = sorted(entries, key=lambda e: e.sequence_number)
    if not ordered:
        return VerificationResult(valid=True, entries_checked=0, detail="Ledger is empty.")

    if ordered[0].sequence_number != 0 or ordered[0].event_type != LedgerEventType.GENESIS:
        return VerificationResult(
            valid=False, entries_checked=0, first_broken_sequence_number=ordered[0].sequence_number,
            detail="Chain does not start with a genesis entry at sequence_number=0.",
        )

    for index, entry in enumerate(ordered):
        expected_prev_hash = GENESIS_PREV_HASH if index == 0 else ordered[index - 1].entry_hash
        if entry.prev_hash != expected_prev_hash:
            return VerificationResult(
                valid=False, entries_checked=index + 1, first_broken_sequence_number=entry.sequence_number,
                detail=f"Entry {entry.sequence_number}'s prev_hash does not match entry "
                       f"{entry.sequence_number - 1}'s entry_hash — an entry was likely "
                       f"deleted, reordered, or inserted out of sequence.",
            )

        recomputed = compute_entry_hash(entry.sequence_number, entry.event_type, entry.payload, entry.prev_hash)
        if recomputed != entry.entry_hash:
            return VerificationResult(
                valid=False, entries_checked=index + 1, first_broken_sequence_number=entry.sequence_number,
                detail=f"Entry {entry.sequence_number}'s stored hash does not match its "
                       f"recomputed hash — its payload was likely edited after creation.",
            )

    return VerificationResult(valid=True, entries_checked=len(ordered), detail="Chain intact.")
