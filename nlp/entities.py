"""
nlp/entities.py

Entity dataclass, type enum, normalisation and extraction confidence.

Lives in its own module so both nlp/extraction.py (the orchestrator) and
nlp/indian_rules.py (the rule layer) can build entities without a
circular import. nlp/extraction.py re-exports everything here, so
existing `from nlp.extraction import ExtractedEntity, EntityType` call
sites keep working unchanged.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from nlp.gazetteer import (
    FIRST_NAMES,
    HONORIFICS,
    MOHAMMAD_VARIANTS,
    NICKNAMES,
    SURNAMES,
)
from nlp.translit import canonical_place, devanagari_to_roman, has_devanagari


class EntityType(str, Enum):
    PERSON = "PERSON"
    LOCATION = "LOCATION"
    PHONE = "PHONE"
    VEHICLE = "VEHICLE"
    ORGANIZATION = "ORGANIZATION"
    ACCOUNT = "ACCOUNT"  # bank account / UPI id


@dataclass
class ExtractedEntity:
    id: str
    text: str
    entity_type: EntityType
    source_doc_id: str
    start_char: int
    end_char: int
    confidence: float = 0.0
    normalized_text: Optional[str] = None
    metadata: dict = field(default_factory=dict)


_WHITESPACE_RUN = re.compile(r"\s+")
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_ORG_SUFFIX_WORDS = {"ltd", "pvt", "llp", "co"}
_PLACE_ABBREVIATIONS = {"rd": "Road", "ext": "Extension"}


def _title_word(word: str) -> str:
    # Preserve short all-caps acronyms (MG, ISBT) instead of title-casing them
    if word.isupper() and len(word) <= 4:
        return word
    return word.title()


def _normalize_person(raw: str) -> str:
    if has_devanagari(raw):
        raw = devanagari_to_roman(raw)
    raw = raw.replace("@", " ")
    tokens = []
    for tok in _WHITESPACE_RUN.split(raw.strip()):
        bare = re.sub(r"[^A-Za-z]", "", tok).lower()
        if not bare:
            continue
        if bare in HONORIFICS:
            continue
        if bare in MOHAMMAD_VARIANTS:
            tokens.append("Mohammad")
            continue
        if len(bare) == 1:  # initial, keep as "S."
            tokens.append(bare.upper() + ".")
            continue
        tokens.append(bare.title())
    return " ".join(tokens)


def normalize_entity(entity: ExtractedEntity) -> str:
    """Clean casing/punctuation before resolution.

    PHONE: digits only, drop +91/91/leading-0 country/trunk prefix.
    VEHICLE: uppercase, strip whitespace/hyphens.
    ACCOUNT: digits (bank account) or lower-case UPI id.
    PERSON: transliterate Devanagari, strip honorifics, canonicalise
        Mohd/Md/Mohammed -> Mohammad, Title Case, initials as "S.".
    LOCATION/ORGANIZATION: Devanagari place -> English, collapse
        whitespace, drop dots inside abbreviations ("M.G." -> "MG").
    """
    raw = entity.text.strip()
    if entity.entity_type in (EntityType.PHONE, EntityType.ACCOUNT):
        raw = raw.translate(_DEVANAGARI_DIGITS)

    if entity.entity_type == EntityType.PHONE:
        digits = re.sub(r"[^0-9]", "", raw)
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        return digits

    if entity.entity_type == EntityType.VEHICLE:
        return re.sub(r"[\s-]", "", raw).upper()

    if entity.entity_type == EntityType.ACCOUNT:
        if "@" in raw:
            return raw.lower()
        return re.sub(r"[^0-9]", "", raw)

    if entity.entity_type == EntityType.PERSON:
        return _normalize_person(raw)

    if entity.entity_type == EntityType.LOCATION and has_devanagari(raw):
        raw = canonical_place(raw)

    raw = re.sub(r"\b([A-Za-z])\.(?=[A-Za-z]\b|\s|$)", r"\1", raw)  # M.G. -> MG
    collapsed = _WHITESPACE_RUN.sub(" ", raw).strip(" ,;:")
    words = []
    for w in collapsed.split(" "):
        lw = w.lower().rstrip(".")
        if lw in _PLACE_ABBREVIATIONS and entity.entity_type == EntityType.LOCATION:
            words.append(_PLACE_ABBREVIATIONS[lw])
        else:
            words.append(_title_word(w))
    return " ".join(words)


_ENGINE_BASE_CONFIDENCE = {
    "regex": 0.90,
    "rule:alias": 0.85,
    "rule:devanagari": 0.80,
    "rule:gazetteer": 0.80,
    "rule:recovered": 0.65,
}


def confidence_score(entity: ExtractedEntity) -> float:
    """Extraction confidence in [0.0, 1.0].

    Base per engine (regex 0.90, alias rule 0.85, gazetteer/Devanagari
    rules 0.80, spaCy 0.75, recovered-by-pattern 0.65, HF = model score),
    nudged by evidence the text itself gives: PERSON tokens found in the
    Indian-name gazetteer (+0.05), a single unknown-word PERSON (-0.10),
    a location corrected by gazetteer (+0.05). Clamped to [0.05, 0.99].
    """
    engine = entity.metadata.get("engine")

    if engine == "huggingface" and "hf_score" in entity.metadata:
        return float(entity.metadata["hf_score"])

    if entity.entity_type in (EntityType.PHONE, EntityType.VEHICLE, EntityType.ACCOUNT):
        return 0.9

    score = _ENGINE_BASE_CONFIDENCE.get(engine, 0.75)

    if entity.entity_type == EntityType.PERSON:
        toks = [t for t in re.split(r"[\s.]+", (entity.normalized_text or entity.text).lower()) if t]
        known = [t for t in toks if t in FIRST_NAMES or t in SURNAMES or t in NICKNAMES]
        if toks and len(known) == len(toks):
            score += 0.05
        elif not known:
            score -= 0.10
        if len(toks) == 1:
            score -= 0.05
    elif entity.metadata.get("correction_applied"):
        score += 0.05

    return max(0.05, min(0.99, round(score, 3)))


def make_entity(text: str, entity_type: EntityType, source_doc_id: str,
                start: int, end: int, engine: Optional[str] = None,
                **metadata) -> ExtractedEntity:
    """Build an ExtractedEntity with normalised text and confidence set."""
    entity = ExtractedEntity(
        id=f"ent-{uuid.uuid4().hex[:12]}",
        text=text,
        entity_type=entity_type,
        source_doc_id=source_doc_id,
        start_char=start,
        end_char=end,
    )
    if engine:
        entity.metadata["engine"] = engine
    entity.metadata.update(metadata)
    entity.normalized_text = normalize_entity(entity)
    entity.confidence = confidence_score(entity)
    return entity
