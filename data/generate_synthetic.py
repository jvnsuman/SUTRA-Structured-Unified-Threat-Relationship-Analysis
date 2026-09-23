"""
data/generate_synthetic.py

Synthetic data generator for FIRs, CDRs, and financial records. All
generated documents are entirely fictional.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

try:
    from faker import Faker
except ImportError as exc:  # pragma: no cover - environment guard
    raise ImportError(
        "faker is required for data/generate_synthetic.py. Install with:\n"
        "    pip install faker --break-system-packages\n"
        "(see requirements.txt — faker>=25.0)"
    ) from exc


_fake = Faker("en_IN")


class DocumentType(str, Enum):
    FIR = "FIR"
    CDR = "CDR"
    FINANCIAL_RECORD = "FINANCIAL_RECORD"


@dataclass
class SyntheticDocument:
    """A single generated document.

    Attributes:
        doc_id: stable identifier, format "<type-prefix>-<hex>".
        doc_type: one of DocumentType.
        text: free-text content (populated for FIR; empty for CDR/financial).
        structured: structured field dict (populated for CDR/financial; empty for FIR).
        embedded_entities: (text, entity_type) pairs deliberately planted
            in this document, used by evaluate_extraction_recall().
    """

    doc_id: str
    doc_type: DocumentType
    text: str = ""
    structured: dict = field(default_factory=dict)
    embedded_entities: list[tuple[str, str]] = field(default_factory=list)


_ORG_SUFFIXES = ("Traders", "Enterprises", "Pvt Ltd", "Trust", "Associates")
_LOCATION_HINTS = ("Road", "Nagar", "Colony", "Chowk", "Marg")


def generate_person_name() -> str:
    """A plausible full name, Title Case, 2 tokens."""
    return _fake.name()


def generate_phone_number() -> str:
    """A 10-digit Indian mobile number starting 6-9, formatted as
    "691-563-7510"."""
    first_digit = random.choice("6789")
    rest = "".join(random.choices("0123456789", k=9))
    number = first_digit + rest
    return f"{number[:3]}-{number[3:6]}-{number[6:]}"


def generate_vehicle_plate() -> str:
    """An Indian-style vehicle registration, e.g. "MH12AB1234"."""
    state_codes = ("MH", "DL", "KA", "TN", "UP", "WB", "GJ", "RJ")
    state = random.choice(state_codes)
    rto = f"{random.randint(1, 99):02d}"
    series = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=2))
    digits = f"{random.randint(1, 9999):04d}"
    return f"{state}{rto}{series}{digits}"


def generate_location() -> str:
    """A plausible location string with a hint word, e.g. "MG Road",
    "Shastri Nagar"."""
    prefix = random.choice([
        "MG", "Station", "Model", _fake.first_name(), _fake.last_name(),
        _fake.city(),
    ])
    hint = random.choice(_LOCATION_HINTS)
    return f"{prefix} {hint}"


def generate_organization_name() -> str:
    """A plausible org name ending in a common suffix, e.g. "Sharma
    Traders"."""
    base = _fake.last_name()
    suffix = random.choice(_ORG_SUFFIXES)
    return f"{base} {suffix}"


def generate_bank_account_number() -> str:
    """An 11-14 digit bank account number."""
    return "".join(random.choices("0123456789", k=random.choice([11, 12, 14])))


_FIR_TEMPLATES = [
    (
        "On {date}, complainant {complainant} reported that {suspect}, "
        "last seen near {location}, was contacted from phone number "
        "{phone}. The suspect's vehicle, registered as {vehicle}, was "
        "spotted outside {organization}."
    ),
    (
        "FIR registered on {date} following information that {suspect} "
        "was operating in the {location} area. A call was traced to "
        "{phone}. Investigators noted a vehicle bearing registration "
        "{vehicle} frequently parked near {organization}."
    ),
    (
        "Complainant {complainant} stated that on {date}, an "
        "unidentified individual using phone number {phone} contacted "
        "her near {location}. The individual, later identified as "
        "{suspect}, was linked to {organization} through vehicle "
        "{vehicle}."
    ),
]


def generate_synthetic_fir(
    doc_id: Optional[str] = None,
    suspect_name: Optional[str] = None,
    suspect_phone: Optional[str] = None,
) -> SyntheticDocument:
    """Generate a single synthetic FIR-style free-text document.

    Args:
        doc_id: optional stable id; if omitted, one is generated.
        suspect_name: optional fixed name for the suspect entity,
            instead of a freshly randomized one. Lets a caller
            deliberately plant the SAME person across multiple
            documents/cases (see scripts/seed_bulk_cases.py's
            cross-case overlap scenarios) rather than every FIR
            getting a wholly independent random suspect.
        suspect_phone: optional fixed phone number for the phone
            entity mentioned in this FIR, same rationale as
            suspect_name — lets a caller plant a phone number that
            also appears in a CDR in a different case.

    Returns:
        A SyntheticDocument with doc_type=FIR, populated `text`, and
        `embedded_entities` recording what was planted.
    """
    complainant = generate_person_name()
    suspect = suspect_name or generate_person_name()
    location = generate_location()
    phone = suspect_phone or generate_phone_number()
    vehicle = generate_vehicle_plate()
    organization = generate_organization_name()
    date = _fake.date_between(start_date="-1y", end_date="today").strftime("%d %B")

    template = random.choice(_FIR_TEMPLATES)
    text = template.format(
        date=date, complainant=complainant, suspect=suspect,
        location=location, phone=phone, vehicle=vehicle,
        organization=organization,
    )

    resolved_id = doc_id or f"fir-{uuid.uuid4().hex[:12]}"

    return SyntheticDocument(
        doc_id=resolved_id,
        doc_type=DocumentType.FIR,
        text=text,
        embedded_entities=[
            (complainant, "PERSON"), (suspect, "PERSON"),
            (location, "LOCATION"), (phone, "PHONE"),
            (vehicle, "VEHICLE"), (organization, "ORGANIZATION"),
        ],
    )


def generate_synthetic_cdr(doc_id: Optional[str] = None,
                            num_calls: int = 5,
                            burst: bool = False,
                            caller_phone: Optional[str] = None) -> SyntheticDocument:
    """Generate a synthetic CDR-style structured record: a list of
    calls between two phone numbers over a time window.

    Args:
        doc_id: optional stable id; if omitted, one is generated.
        num_calls: how many call records to generate.
        burst: if True, calls are clustered within a short window
            (minutes) rather than spread across a day.
        caller_phone: optional fixed phone number for the caller side
            of every call, instead of a freshly randomized one — see
            generate_synthetic_fir's suspect_phone for the same
            cross-document-planting rationale.

    Returns:
        A SyntheticDocument with doc_type=CDR and `structured` containing
        a `calls` list of {caller, callee, timestamp, duration_seconds}.
    """
    caller = caller_phone or generate_phone_number()
    callee = generate_phone_number()

    calls = []
    if burst:
        base_time = _fake.date_time_between(start_date="-30d", end_date="now")
        for _ in range(num_calls):
            offset = timedelta(minutes=random.randint(0, 15))
            calls.append({
                "caller": caller,
                "callee": callee,
                "timestamp": (base_time + offset).isoformat(),
                "duration_seconds": random.randint(5, 120),
            })
    else:
        for _ in range(num_calls):
            timestamp = _fake.date_time_between(start_date="-30d", end_date="now")
            calls.append({
                "caller": caller,
                "callee": callee,
                "timestamp": timestamp.isoformat(),
                "duration_seconds": random.randint(10, 600),
            })

    resolved_id = doc_id or f"cdr-{uuid.uuid4().hex[:12]}"

    return SyntheticDocument(
        doc_id=resolved_id,
        doc_type=DocumentType.CDR,
        structured={"calls": calls, "is_burst_pattern": burst},
        embedded_entities=[(caller, "PHONE"), (callee, "PHONE")],
    )


def generate_synthetic_financial_record(
    doc_id: Optional[str] = None,
    num_transactions: int = 4,
    structuring_pattern: bool = False,
) -> SyntheticDocument:
    """Generate a synthetic financial-transaction record: a
    sender/receiver pair with a list of transactions.

    Args:
        doc_id: optional stable id; if omitted, one is generated.
        num_transactions: how many transactions to generate.
        structuring_pattern: if True, generates several transactions
            each just under a 50,000 threshold instead of one large
            transaction (the "structuring/layering" pattern).

    Returns:
        A SyntheticDocument with doc_type=FINANCIAL_RECORD and
        `structured` containing sender/receiver account numbers and a
        `transactions` list.
    """
    sender_account = generate_bank_account_number()
    receiver_account = generate_bank_account_number()

    transactions = []
    if structuring_pattern:
        threshold = 50_000
        for _ in range(num_transactions):
            amount = random.randint(int(threshold * 0.85), threshold - 1)
            timestamp = _fake.date_time_between(start_date="-14d", end_date="now")
            transactions.append({
                "amount": amount,
                "timestamp": timestamp.isoformat(),
                "currency": "INR",
            })
    else:
        for _ in range(num_transactions):
            amount = random.randint(500, 200_000)
            timestamp = _fake.date_time_between(start_date="-90d", end_date="now")
            transactions.append({
                "amount": amount,
                "timestamp": timestamp.isoformat(),
                "currency": "INR",
            })

    resolved_id = doc_id or f"fin-{uuid.uuid4().hex[:12]}"

    return SyntheticDocument(
        doc_id=resolved_id,
        doc_type=DocumentType.FINANCIAL_RECORD,
        structured={
            "sender_account": sender_account,
            "receiver_account": receiver_account,
            "transactions": transactions,
            "is_structuring_pattern": structuring_pattern,
        },
        embedded_entities=[],
    )


def generate_dataset(num_firs: int = 10, num_cdrs: int = 5,
                      num_financial: int = 5,
                      burst_fraction: float = 0.3,
                      structuring_fraction: float = 0.3) -> list[SyntheticDocument]:
    """Generate a batch of mixed synthetic documents.

    Args:
        num_firs, num_cdrs, num_financial: how many of each document type.
        burst_fraction: fraction of CDRs generated with burst=True.
        structuring_fraction: fraction of financial records generated
            with structuring_pattern=True.

    Returns:
        A flat list of SyntheticDocument, order: FIRs, then CDRs, then
        financial records.
    """
    docs: list[SyntheticDocument] = []
    docs.extend(generate_synthetic_fir() for _ in range(num_firs))
    docs.extend(
        generate_synthetic_cdr(burst=random.random() < burst_fraction)
        for _ in range(num_cdrs)
    )
    docs.extend(
        generate_synthetic_financial_record(
            structuring_pattern=random.random() < structuring_fraction
        )
        for _ in range(num_financial)
    )
    return docs


def evaluate_extraction_recall(fir_docs: list[SyntheticDocument]) -> dict:
    """For a batch of generated FIRs, run nlp.extraction.extract_entities()
    and report what fraction of embedded_entities were recovered.

    PERSON/LOCATION/ORGANIZATION use substring containment (extraction
    may include a surrounding word, e.g. "Complainant Kalpit Dube" for
    planted "Kalpit Dube"); PHONE/VEHICLE use exact match.

    Args:
        fir_docs: documents with doc_type == DocumentType.FIR.

    Returns:
        dict with keys: total_planted, total_found, recall (float),
        misses (list of (text, expected_type) tuples not found).
    """
    from nlp.extraction import extract_entities

    total_planted = 0
    total_found = 0
    misses: list[tuple[str, str]] = []

    for doc in fir_docs:
        if doc.doc_type != DocumentType.FIR:
            continue
        found = extract_entities(doc.text, doc.doc_id)
        found_texts = [e.text for e in found]

        for planted_text, planted_type in doc.embedded_entities:
            total_planted += 1
            if planted_type in ("PHONE", "VEHICLE"):
                hit = planted_text in found_texts
            else:
                hit = any(planted_text in ft for ft in found_texts)

            if hit:
                total_found += 1
            else:
                misses.append((planted_text, planted_type))

    recall = total_found / total_planted if total_planted else 0.0
    return {
        "total_planted": total_planted,
        "total_found": total_found,
        "recall": recall,
        "misses": misses,
    }


if __name__ == "__main__":
    dataset = generate_dataset(num_firs=3, num_cdrs=2, num_financial=2)

    for doc in dataset:
        print(f"--- {doc.doc_id} ({doc.doc_type.value}) ---")
        if doc.text:
            print(doc.text)
        if doc.structured:
            print(doc.structured)
        print()

    fir_docs = [d for d in dataset if d.doc_type == DocumentType.FIR]
    try:
        report = evaluate_extraction_recall(fir_docs)
        print("--- Extraction recall check ---")
        print(f"Planted: {report['total_planted']}, Found: {report['total_found']}, "
              f"Recall: {report['recall']:.0%}")
        if report["misses"]:
            print("Missed entities:", report["misses"])
    except ImportError:
        print("(nlp.extraction not importable from this context — skipping recall check)")
