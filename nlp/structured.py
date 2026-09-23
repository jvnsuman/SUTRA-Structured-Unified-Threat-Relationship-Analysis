"""
nlp/structured.py

Structured CDR and financial records -> entities + relations, directly,
without going through prose extraction.

A CDR row already says exactly who called whom and when; running NER over
a sentence rendered from it (the old approach) throws that precision away
and invites phantom entities. Here each distinct phone/account becomes one
entity mention and each distinct pair one edge, with the call/transaction
count and time window kept in the edge's source_text so the evidence trail
shows what backs it.

Accepted shapes (see data/generate_synthetic.py for real examples):

  CDR:       {"calls": [{"caller": "98110-22334", "callee": "9898989898",
                         "timestamp": "2026-08-01T10:00:00", "duration_seconds": 42}, ...],
              "subscribers": {"9811022334": "Suresh Yadav"}}        # optional
  Financial: {"sender_account": "123456789012", "receiver_account": "...",
              "transactions": [{"amount": 49000, "timestamp": "...", "currency": "INR"}, ...],
              "sender_name": "...", "receiver_name": "..."}          # optional
"""

from __future__ import annotations

import re
import uuid
from typing import Optional

from nlp.entities import EntityType, ExtractedEntity, make_entity
from nlp.relation_classification import ClassifiedRelation, RelationType


def _phone(raw) -> Optional[str]:
    digits = re.sub(r"[^0-9]", "", str(raw or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def _account(raw) -> Optional[str]:
    digits = re.sub(r"[^0-9]", "", str(raw or ""))
    return digits if 9 <= len(digits) <= 18 else None


def _rel(a: ExtractedEntity, b: ExtractedEntity, rtype: RelationType, text: str) -> ClassifiedRelation:
    return ClassifiedRelation(
        id=f"rel-{uuid.uuid4().hex[:12]}", entity_a_id=a.id, entity_b_id=b.id,
        relation_type=rtype, confidence=0.99, source_doc_id=a.source_doc_id, source_text=text,
    )


def _entity(value: str, etype: EntityType, doc_id: str, cache: dict) -> ExtractedEntity:
    key = (etype, value)
    if key not in cache:
        cache[key] = make_entity(value, etype, doc_id, 0, 0, engine="structured", structured=True)
    return cache[key]


def parse_structured(document_id: str, document_type: str, structured: dict) -> tuple:
    """Returns (entities, relations, warnings). Bad rows are skipped and
    reported, never fatal."""
    entities_by_key: dict = {}
    relations: list = []
    warnings: list = []
    structured = structured or {}

    if "calls" in structured:
        pair_stats: dict = {}
        for idx, call in enumerate(structured.get("calls") or []):
            a, b = _phone(call.get("caller")), _phone(call.get("callee"))
            if not a or not b:
                warnings.append(f"call #{idx + 1}: unreadable caller/callee number, skipped")
                continue
            if a == b:
                continue
            stat = pair_stats.setdefault((a, b), {"n": 0, "secs": 0, "times": []})
            stat["n"] += 1
            stat["secs"] += int(call.get("duration_seconds") or 0)
            if call.get("timestamp"):
                stat["times"].append(str(call["timestamp"]))
        subscribers = structured.get("subscribers") or {}
        for (a, b), stat in pair_stats.items():
            ea = _entity(a, EntityType.PHONE, document_id, entities_by_key)
            eb = _entity(b, EntityType.PHONE, document_id, entities_by_key)
            window = f" between {min(stat['times'])} and {max(stat['times'])}" if stat["times"] else ""
            relations.append(_rel(ea, eb, RelationType.CALLS,
                                  f"CDR: {a} called {b} {stat['n']} time(s), {stat['secs']}s total{window}"))
        for number, name in subscribers.items():
            num = _phone(number)
            if num and name and str(name).strip():
                phone_e = _entity(num, EntityType.PHONE, document_id, entities_by_key)
                person = make_entity(str(name).strip(), EntityType.PERSON, document_id, 0, 0,
                                     engine="structured", structured=True, nearby_ids=[f"PHONE:{num}"])
                entities_by_key[(EntityType.PERSON, f"{name}|{num}")] = person
                relations.append(_rel(person, phone_e, RelationType.CALLS, f"CDR subscriber record: {name} is the subscriber of {num}"))

    if "transactions" in structured:
        sender, receiver = _account(structured.get("sender_account")), _account(structured.get("receiver_account"))
        txs = structured.get("transactions") or []
        if not sender or not receiver:
            warnings.append("financial record: missing/invalid sender_account or receiver_account, skipped")
        elif txs:
            amounts = []
            for tx in txs:
                try:
                    amounts.append(float(tx.get("amount")))
                except (TypeError, ValueError):
                    warnings.append("a transaction has a non-numeric amount and was ignored")
            currency = (txs[0].get("currency") or "INR") if isinstance(txs[0], dict) else "INR"
            es = _entity(sender, EntityType.ACCOUNT, document_id, entities_by_key)
            er = _entity(receiver, EntityType.ACCOUNT, document_id, entities_by_key)
            times = [str(t["timestamp"]) for t in txs if isinstance(t, dict) and t.get("timestamp")]
            window = f" between {min(times)} and {max(times)}" if times else ""
            relations.append(_rel(es, er, RelationType.TRANSACTS_WITH,
                                  f"Financial record: {len(amounts)} transaction(s) from account {sender} to {receiver}, "
                                  f"total {sum(amounts):,.0f} {currency}, largest {max(amounts, default=0):,.0f}{window}"))
            for role, acct_entity, nm_key in (("sender", es, "sender_name"), ("receiver", er, "receiver_name")):
                name = (structured.get(nm_key) or "").strip()
                if name:
                    acct = acct_entity.normalized_text
                    person = make_entity(name, EntityType.PERSON, document_id, 0, 0,
                                         engine="structured", structured=True, nearby_ids=[f"ACCOUNT:{acct}"])
                    entities_by_key[(EntityType.PERSON, f"{name}|{acct}")] = person
                    relations.append(_rel(person, acct_entity, RelationType.ASSOCIATED_WITH,
                                          f"Financial record: {name} is the {role} account holder ({acct})"))

    return list(entities_by_key.values()), relations, warnings
