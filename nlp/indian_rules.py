"""
nlp/indian_rules.py

The Indian-FIR rule layer that sits around spaCy in nlp/extraction.py.

spaCy's small English model was measured (see docs/EVALUATION.md) to
mis-handle typical FIR text: villages tagged PERSON, nicknames tagged
LOCATION, "Mohd. Irfan @ Guddu" split into fragments, vehicle plates
with 3-letter series missed, ALL-CAPS names missed entirely, payment
acronyms (UPI) tagged ORG. This module adds deterministic, auditable
rules for exactly those cases. Nothing here replaces spaCy; every rule
either *adds* an entity spaCy missed, *fixes the type/span* of one it got
wrong, or *drops* a known non-entity. Rule-produced entities carry
metadata["engine"] so an investigator can see where each came from.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from functools import lru_cache
from typing import Optional

from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein

from nlp.entities import EntityType, ExtractedEntity, confidence_score, make_entity, normalize_entity
from nlp.gazetteer import (
    CITIES_STATES,
    COMMON_WORDS,
    ROLE_WORDS,
    COMMON_VILLAGE_NAMES,
    DEVANAGARI_CUE_STOPWORDS,
    DEVANAGARI_FIRST_NAMES,
    DEVANAGARI_PLACE_SUFFIXES,
    DEVANAGARI_PLACES,
    DEVANAGARI_SURNAMES,
    FIRST_NAMES,
    HONORIFICS,
    MOHAMMAD_VARIANTS,
    NICKNAMES,
    NON_ENTITY_ACRONYMS,
    PLACE_CUE_WORDS,
    PLACE_SUFFIXES,
    SURNAMES,
    VEHICLE_MAKES,
)

_NAMEISH = FIRST_NAMES | SURNAMES | NICKNAMES | MOHAMMAD_VARIANTS
_TRIM_WORDS = HONORIFICS | ROLE_WORDS
_NAME_LIST = sorted(FIRST_NAMES | NICKNAMES | SURNAMES)


@lru_cache(maxsize=8192)
def fuzzy_name(token: str) -> Optional[str]:
    """The gazetteer name a (possibly misspelled) token most likely is, or
    None. Exact matches are returned as-is; otherwise a Damerau-Levenshtein
    similarity >= 0.8 on tokens of 5+ letters ("Surehs" -> "suresh",
    "Irfna" -> "irfan"). Fuzzy hits are only trusted next to other name
    evidence (see _is_name_run)."""
    tok = re.sub(r"[^a-z]", "", token.lower())
    if tok in _NAMEISH:
        return tok
    if len(tok) < 5:
        return None
    best = process.extractOne(tok, _NAME_LIST, scorer=DamerauLevenshtein.normalized_similarity, score_cutoff=0.79)
    return best[0] if best else None


def _is_name_run(tokens: list) -> bool:
    """True if every token is a (fuzzy) gazetteer name AND at least one is an
    exact match, so a lone misspelling of a common word never counts."""
    low = [re.sub(r"[^a-z]", "", t.lower()) for t in tokens]
    return bool(low) and all(fuzzy_name(t) for t in low) and any(t in _NAMEISH for t in low)
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

_SENTENCE_STARTERS = frozenset({
    "the", "on", "a", "an", "by", "in", "at", "one", "this", "that", "it", "he", "she",
    "they", "as", "after", "before", "during", "when", "while", "with", "from", "to", "for",
    "and", "but", "also", "then", "thereafter", "during", "according", "further",
})

# ---------------------------------------------------------------------------
# Text pre-processing
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"(?<![\w])[A-Za-z][A-Za-z'\-]*(?![\w])")
_KEEP_UPPER = NON_ENTITY_ACRONYMS | {"isbt", "mg", "ps", "uk", "usa", "up", "mp", "hp", "ap", "wb", "tn"}


def normalize_digits(text: str) -> str:
    """Devanagari digits -> ASCII, same length, so char offsets stay valid."""
    return text.translate(_DEVANAGARI_DIGITS)


def recase_shouting(text: str) -> str:
    """Title-case ALL-CAPS words (same length!) so spaCy can see names in
    shouted text ("RAMESH SHARMA seen at bus stand"). Acronyms (UPI, FIR,
    ISBT, MG), 1-2 letter tokens and tokens containing digits (plates) are
    left alone. Offsets are unchanged, so entity spans map straight back
    onto the original text.
    """
    def _fix(match: re.Match) -> str:
        word = match.group()
        letters = re.sub(r"[^A-Za-z]", "", word)
        if not word.isupper() or letters.lower() in _KEEP_UPPER:
            return word
        if letters.lower() in COMMON_WORDS:
            return word.lower()          # "ON MOBILE" must not become "On Mobile"
        if len(letters) < 3:
            return word
        return word.title()

    return _WORD_RE.sub(_fix, text)


# ---------------------------------------------------------------------------
# Identifiers: accounts, UPI ids
# ---------------------------------------------------------------------------

_ACCOUNT_CUE = re.compile(
    r"(?:a/c|acc(?:ount)?|खाता)\.?(?:\s*(?:no\.?|number|संख्या|नंबर))?\s*[:\-#]?\s*(\d{9,18})(?!\d)",
    re.IGNORECASE,
)
_UPI_ID = re.compile(
    r"(?<![\w.])[\w.\-]{2,}@(?:ok(?:axis|hdfcbank|icici|sbi)|ybl|ibl|axl|paytm|upi|apl|sbi|"
    r"hdfcbank|icici|axisbank|pnb|barodampay)\b",
    re.IGNORECASE,
)


def find_account_entities(text: str, doc_id: str) -> list:
    """Bank account numbers (only with an explicit a/c cue, so a bare
    10-digit number stays a phone) and UPI ids."""
    entities = []
    for m in _ACCOUNT_CUE.finditer(text):
        entities.append(make_entity(m.group(1), EntityType.ACCOUNT, doc_id, m.start(1), m.end(1), engine="regex"))
    for m in _UPI_ID.finditer(text):
        entities.append(make_entity(m.group(), EntityType.ACCOUNT, doc_id, m.start(), m.end(), engine="regex"))
    return entities


# ---------------------------------------------------------------------------
# Alias markers: "X alias Y", "X @ Y", "X urf Y", "X a.k.a. Y"
# ---------------------------------------------------------------------------

_ALIAS_MARK = re.compile(
    r"(?:\b(?:alias|urf|aka|known\s+as|also\s+known\s+as|popularly\s+known\s+as|nicknamed)\b"
    r"|\ba\.k\.a\.?(?=\s)"
    r"|(?<=\s)@(?=\s|[A-Z\"'“‘])|(?<=[A-Za-z.])@(?=\s))",
    re.IGNORECASE,
)
_CAP_TOKEN = re.compile(r"[A-Z][A-Za-z'\-]*\.?")
_QUOTES = "\"'“”‘’`"


def _trailing_name_tokens(prefix: str) -> list:
    """Trailing run of capitalised tokens before an alias marker, as
    (token, start, end) triples relative to prefix, honorifics/role words
    and sentence-starters trimmed from the left."""
    stripped_len = len(prefix.rstrip(" ,;:(-"))
    prefix = prefix[:stripped_len]
    tokens = [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", prefix)]
    run = []
    for tok, s, e in reversed(tokens):
        clean = tok.strip(_QUOTES + ",;:()")
        if _CAP_TOKEN.fullmatch(clean) and len(run) < 4:
            run.append((clean, s, s + len(clean)))
        else:
            break
    run.reverse()
    while run:
        low = run[0][0].lower().rstrip(".")
        if low in HONORIFICS or low in PLACE_CUE_WORDS or low in _SENTENCE_STARTERS:
            run.pop(0)
        else:
            break
    return run


def _following_alias_tokens(text: str, pos: int) -> list:
    """Alias token(s) after a marker at pos: first any capitalised word,
    then up to 2 more only while they are known name components."""
    i = pos
    while i < len(text) and text[i] in " \t" + _QUOTES:
        i += 1
    out = []
    while i < len(text) and len(out) < 3:
        m = _CAP_TOKEN.match(text, i)
        if not m:
            break
        tok = m.group().rstrip(".")
        low = tok.lower()
        if out and (low not in _NAMEISH or low in PLACE_SUFFIXES):
            break
        if not out and (low in PLACE_SUFFIXES or low in NON_ENTITY_ACRONYMS or low in CITIES_STATES):
            break
        out.append((tok, m.start(), m.start() + len(tok)))
        j = m.end()
        while j < len(text) and text[j] == " ":
            j += 1
        i = j
    return out


def find_alias_groups(text: str, doc_id: str) -> list:
    """Extract "Primary alias Alias" pairs as PERSON entities that share an
    `alias_group` id (an explicit same-person assertion in the source
    document — the strongest merge signal nlp/resolution.py uses).
    Handles chains ("A alias B alias C").
    """
    entities: list = []
    consumed_until = -1
    for mark in _ALIAS_MARK.finditer(text):
        if mark.start() < consumed_until:
            continue
        primary = _trailing_name_tokens(text[:mark.start()])
        if not primary:
            continue
        group_id = f"alias-{uuid.uuid4().hex[:10]}"
        p_start, p_end = primary[0][1], primary[-1][2]
        members = [make_entity(text[p_start:p_end].rstrip(" ,;"), EntityType.PERSON, doc_id, p_start, p_end,
                               engine="rule:alias", alias_group=group_id, alias_role="primary")]
        cursor = mark.end()
        while True:
            alias = _following_alias_tokens(text, cursor)
            if not alias:
                break
            a_start, a_end = alias[0][1], alias[-1][2]
            members.append(make_entity(text[a_start:a_end], EntityType.PERSON, doc_id, a_start, a_end,
                                       engine="rule:alias", alias_group=group_id, alias_role="alias"))
            cursor = a_end
            nxt = _ALIAS_MARK.match(text, _skip_sep(text, cursor))
            if not nxt:
                break
            cursor = nxt.end()
        if len(members) >= 2:
            entities.extend(members)
            consumed_until = cursor
    return entities


def _skip_sep(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in " ,/":
        pos += 1
    return pos


# ---------------------------------------------------------------------------
# Devanagari (Hindi) entities
# ---------------------------------------------------------------------------

_DEV_WORD = re.compile(r"[\u0900-\u097F]+")


def find_devanagari_entities(text: str, doc_id: str, claimed: list) -> list:
    """Gazetteer + suffix rules for Hindi FIR text. Names are normalised
    by transliteration (nlp/translit.py) so "राजू कुमार" resolves to the
    same person as "Raju Kumar". Places map to canonical English."""
    text_nfc = unicodedata.normalize("NFC", text)
    if len(text_nfc) != len(text):  # never let offsets drift; skip if NFC changed length
        text_nfc = text
    words = [(m.group(), m.start(), m.end()) for m in _DEV_WORD.finditer(text_nfc)]
    if not words:
        return []

    def _adjacent(i: int) -> bool:  # words[i] and words[i+1] separated by exactly one space
        return i + 1 < len(words) and text_nfc[words[i][2]:words[i + 1][1]] == " "

    entities: list = []
    used = [False] * len(words)

    def _free(i: int) -> bool:
        return not used[i] and not any(words[i][1] < ce and cs < words[i][2] for cs, ce in claimed)

    # places: 2-word then 1-word gazetteer, then "<word> <suffix>"
    for i in range(len(words)):
        if not _free(i):
            continue
        if _adjacent(i) and _free(i + 1) and f"{words[i][0]} {words[i + 1][0]}" in DEVANAGARI_PLACES:
            _emit(entities, text, doc_id, words[i][1], words[i + 1][2], EntityType.LOCATION, used, (i, i + 1))
        elif words[i][0] in DEVANAGARI_PLACES:
            _emit(entities, text, doc_id, words[i][1], words[i][2], EntityType.LOCATION, used, (i,))
        elif (_adjacent(i) and _free(i + 1) and words[i + 1][0] in DEVANAGARI_PLACE_SUFFIXES
              and words[i][0] not in DEVANAGARI_CUE_STOPWORDS):
            _emit(entities, text, doc_id, words[i][1], words[i + 1][2], EntityType.LOCATION, used, (i, i + 1))

    # persons: maximal runs of name-gazetteer words containing a first name
    i = 0
    while i < len(words):
        w = words[i][0]
        if not _free(i) or (w not in DEVANAGARI_FIRST_NAMES and w not in DEVANAGARI_SURNAMES):
            i += 1
            continue
        j = i
        while (j + 1 < len(words) and _adjacent(j) and _free(j + 1) and j - i < 2
               and (words[j + 1][0] in DEVANAGARI_FIRST_NAMES or words[j + 1][0] in DEVANAGARI_SURNAMES)):
            j += 1
        run = [words[k][0] for k in range(i, j + 1)]
        if any(t in DEVANAGARI_FIRST_NAMES for t in run):
            _emit(entities, text, doc_id, words[i][1], words[j][2], EntityType.PERSON, used, tuple(range(i, j + 1)))
        i = j + 1
    return entities


def _emit(entities, text, doc_id, start, end, etype, used, idxs) -> None:
    entities.append(make_entity(text[start:end], etype, doc_id, start, end,
                                engine="rule:devanagari", script="devanagari"))
    for k in idxs:
        used[k] = True


# ---------------------------------------------------------------------------
# spaCy post-processing: trim, drop, reclassify, merge
# ---------------------------------------------------------------------------

_STRONG_LOCATION_CUES = re.compile(
    r"(?:\bvillage|\bvill\.?|\br/o|\bresident\s+of|\bnative\s+of|\bdistrict|\bdist\.?|\bp\.?s\.?|\bthana|\bresiding\s+at)\s*$",
    re.IGNORECASE,
)
_PERSON_CUES = re.compile(
    r"(?:\balias|\burf|\baka|\bknown\s+as|@|\bshri|\bsmt\.?|\bmr\.?|\bmrs\.?|\bms\.?|\bkm\.?|\bsh\.?|\baccused|"
    r"\bsuspect|\bcomplainant|\bvictim|\bwitness|\binformant|\bs/o|\bd/o|\bw/o|\bc/o)\s*$",
    re.IGNORECASE,
)


def _bare(token: str) -> str:
    return re.sub(r"[^a-z]", "", token.lower())


def _tokens_lower(text: str) -> list:
    return [b for b in (_bare(t) for t in re.split(r"[\s.]+", text)) if b]


def _retype(entity: ExtractedEntity, new_type: EntityType, engine: str, why: str) -> None:
    entity.entity_type = new_type
    entity.metadata["engine"] = engine
    entity.metadata["correction_applied"] = why
    entity.normalized_text = normalize_entity(entity)
    entity.confidence = confidence_score(entity)


_PLACE_PREFIXES = frozenset({"ps", "thana", "dist", "district", "vill", "village", "tehsil", "block", "post"})


def _trim_entity(entity: ExtractedEntity, text: str) -> Optional[ExtractedEntity]:
    """Strip leading honorific/role words and trailing junk ('@', '.',
    ',') from a PERSON/ORG/LOCATION span; None if nothing remains."""
    start, end = entity.start_char, entity.end_char
    span = text[start:end]
    # leading honorifics, and place-cue prefixes ("PS Thawe", "village Rampur")
    place_prefix = False
    while True:
        m = re.match(r"\s*([A-Za-z]+)\.?\s+", span)
        if m and m.group(1).lower() in _TRIM_WORDS:
            start += m.end()
            span = text[start:end]
        elif m and m.group(1).lower().replace(".", "") in _PLACE_PREFIXES:
            start += m.end()
            span = text[start:end]
            place_prefix = True
        else:
            break
    if place_prefix:
        entity.metadata["_force_location"] = True
    stripped = span.rstrip(" .,;:@-")
    end = start + len(stripped)
    stripped = stripped.lstrip(" ,;:@-")
    start = end - len(stripped)
    if not stripped:
        return None
    if (start, end) != (entity.start_char, entity.end_char):
        entity.start_char, entity.end_char, entity.text = start, end, text[start:end]
        entity.normalized_text = normalize_entity(entity)
        entity.metadata["correction_applied"] = "trimmed_span"
        entity.confidence = confidence_score(entity)
    return entity


_CONNECTOR_RE = re.compile(r"\s(?:at|near|in|from|with|and|to|by|of|on)\s")


def _split_on_connectors(entities: list, text: str, doc_id: str) -> list:
    """spaCy sometimes returns one span across a lower-case connector
    ("Ifran Paswan at Anand Vihar ISBT"). A real name never contains one:
    split into the parts on either side and let the normal passes type
    each part."""
    result: list = []
    for e in entities:
        if e.entity_type == EntityType.PHONE or e.metadata.get("engine", "").startswith(("rule:alias", "regex", "rule:devanagari")):
            result.append(e)
            continue
        pieces, cursor = [], 0
        span_text = text[e.start_char:e.end_char]
        for m in _CONNECTOR_RE.finditer(span_text):
            pieces.append((cursor, m.start()))
            cursor = m.end()
        if not pieces:
            result.append(e)
            continue
        pieces.append((cursor, len(span_text)))
        for a, b in pieces:
            part = span_text[a:b].strip()
            if not part:
                continue
            start = e.start_char + a + (len(span_text[a:b]) - len(span_text[a:b].lstrip()))
            result.append(make_entity(part, e.entity_type, doc_id, start, start + len(part),
                                      engine=e.metadata.get("engine", "spacy"), correction_applied="split_on_connector"))
    return result


def clean_and_reclassify(entities: list, text: str, doc_id: str) -> list:
    """Post-process spaCy PERSON/LOCATION/ORGANIZATION entities."""
    out: list = []
    entities = _split_on_connectors(entities, text, doc_id)
    for e in entities:
        if e.entity_type not in (EntityType.PERSON, EntityType.LOCATION, EntityType.ORGANIZATION):
            out.append(e)
            continue
        if e.metadata.get("engine") in ("rule:alias", "rule:devanagari"):
            out.append(e)
            continue

        e = _trim_entity(e, text)
        if e is None:
            continue
        if e.metadata.pop("_force_location", False) and e.entity_type != EntityType.LOCATION:
            _retype(e, EntityType.LOCATION, "rule:gazetteer", "reclassified_place_prefix")

        toks = _tokens_lower(e.text)
        if not toks or re.search(r"\d", e.text):
            continue
        if all(t in MOHAMMAD_VARIANTS for t in toks):
            continue                      # a bare "Mohd" is a fragment, not a person
        if all(t in COMMON_WORDS or t in ROLE_WORDS for t in toks):
            continue                      # "On Mobile", "Was Noted"
        if all(t in NON_ENTITY_ACRONYMS or t in VEHICLE_MAKES for t in toks):
            continue

        phrase = " ".join(toks)
        before = text[max(0, e.start_char - 40):e.start_char]
        last_word = toks[-1]

        if phrase in _HUB_WORDS:
            if e.entity_type != EntityType.LOCATION:
                _retype(e, EntityType.LOCATION, "rule:gazetteer", "reclassified_transit_hub")
        elif phrase in CITIES_STATES or phrase in COMMON_VILLAGE_NAMES:
            if e.entity_type != EntityType.LOCATION:
                _retype(e, EntityType.LOCATION, "rule:gazetteer", "reclassified_known_place")
        elif len(toks) >= 2 and last_word in PLACE_SUFFIXES and not all(t in _NAMEISH for t in toks[:-1]) \
                or (len(toks) >= 2 and last_word in {"isbt", "vihar", "puri", "bagh", "ganj"}):
            if e.entity_type != EntityType.LOCATION:
                _retype(e, EntityType.LOCATION, "rule:gazetteer", "reclassified_place_suffix")
        elif _STRONG_LOCATION_CUES.search(before) and not all(t in FIRST_NAMES for t in toks):
            if e.entity_type != EntityType.LOCATION:
                _retype(e, EntityType.LOCATION, "rule:gazetteer", "reclassified_location_cue")
        elif _is_name_run(toks) and e.entity_type != EntityType.PERSON:
            _retype(e, EntityType.PERSON, "rule:gazetteer", "reclassified_known_name")
        elif _PERSON_CUES.search(before) and e.entity_type == EntityType.LOCATION and len(toks) <= 3:
            _retype(e, EntityType.PERSON, "rule:gazetteer", "reclassified_person_cue")
        out.append(e)

    out = _extend_person_spans(out, text)
    out = _merge_transit_hubs(out, text)
    out = _merge_sector_city(out, text)
    return _merge_adjacent_persons(out, text)


_HUB_WORDS = frozenset({"bus stand", "bus stop", "railway station", "metro station", "isbt", "junction", "airport"})
_CITY_WORDS = frozenset(CITIES_STATES)
_HUB_TAIL_RE = re.compile(r"(?i)\s+(?:bus\s+stand|bus\s+stop|railway\s+station|metro\s+station)\b")


def _absorb(prev: ExtractedEntity, nxt: ExtractedEntity, text: str, why: str) -> None:
    prev.end_char = nxt.end_char
    prev.text = text[prev.start_char:prev.end_char]
    prev.normalized_text = normalize_entity(prev)
    prev.metadata["correction_applied"] = why
    prev.metadata["engine"] = "rule:gazetteer"
    prev.confidence = confidence_score(prev)


def _merge_transit_hubs(entities: list, text: str) -> list:
    """"Gorakhpur" + "Bus Stand" -> one LOCATION "Gorakhpur Bus Stand"."""
    result: list = []
    for e in sorted(entities, key=lambda x: x.start_char):
        prev = result[-1] if result else None
        if (prev is not None and prev.entity_type == EntityType.LOCATION and e.entity_type == EntityType.LOCATION
                and " ".join(_tokens_lower(e.text)) in _HUB_WORDS
                and text[prev.end_char:e.start_char] == " "):
            _absorb(prev, e, text, "merged_transit_hub")
        else:
            result.append(e)
    # "Gorakhpur" + unclaimed " Bus Stand": grow the location over the hub word
    for e in result:
        if e.entity_type == EntityType.LOCATION and not re.search(r"(?i)\b(stand|stop|station|isbt|junction|airport)$", e.text):
            m = _HUB_TAIL_RE.match(text, e.end_char)
            if m and not any(o is not e and o.start_char < m.end() and e.end_char < o.end_char for o in result):
                e.end_char = m.end()
                e.text = text[e.start_char:e.end_char]
                e.normalized_text = normalize_entity(e)
                e.metadata["correction_applied"] = "extended_transit_hub"
    return result


def _merge_sector_city(entities: list, text: str) -> list:
    """"Sector 14" + "Gurgaon" -> "Sector 14 Gurgaon": sector numbers repeat
    across cities, so the city is part of the place's identity."""
    result: list = []
    for e in sorted(entities, key=lambda x: x.start_char):
        prev = result[-1] if result else None
        if (prev is not None and prev.entity_type == EntityType.LOCATION and e.entity_type == EntityType.LOCATION
                and re.fullmatch(r"(?i)(sector|phase|block)[-\s]?\d{1,3}[a-z]?", prev.text.strip())
                and " ".join(_tokens_lower(e.text)) in _CITY_WORDS
                and re.fullmatch(r"[ ,]{1,2}", text[prev.end_char:e.start_char] or "x")):
            _absorb(prev, e, text, "merged_sector_city")
        else:
            result.append(e)
    return result


_PREV_WORD_RE = re.compile(r"([A-Z][a-z]+\.?)\s$")
_NEXT_WORD_RE = re.compile(r"([A-Z][a-z]+\.?)")


def _extend_person_spans(entities: list, text: str) -> list:
    """spaCy often returns only part of a name ("Sharma" for "Ramesh
    Sharma" at a sentence start). Grow a PERSON span over adjacent
    capitalised words that are known name components, never into another
    entity."""
    spans = [(e.start_char, e.end_char) for e in entities]
    for e in entities:
        if e.entity_type != EntityType.PERSON or e.metadata.get("engine") in ("rule:alias", "rule:devanagari"):
            continue
        others = [sp for sp in spans if sp != (e.start_char, e.end_char)]
        start, end = e.start_char, e.end_char
        for _ in range(2):
            m = _PREV_WORD_RE.search(text[max(0, start - 20):start])
            if not m:
                break
            word = m.group(1)
            new_start = start - len(m.group(0))
            if fuzzy_name(word) and not _overlaps((new_start, start), others):
                start = new_start
            else:
                break
        for _ in range(2):
            gap = text[end:end + 1]
            m = _NEXT_WORD_RE.match(text, end + 1) if gap == " " else None
            if not m:
                break
            if fuzzy_name(m.group(1)) and not _overlaps((end + 1, m.end()), others):
                end = m.end()
            else:
                break
        if (start, end) != (e.start_char, e.end_char):
            e.start_char, e.end_char, e.text = start, end, text[start:end].rstrip(" .,")
            e.end_char = start + len(e.text)
            e.normalized_text = normalize_entity(e)
            e.metadata["correction_applied"] = "extended_partial_name"
            e.confidence = confidence_score(e)
            spans = [(x.start_char, x.end_char) for x in entities]
    return entities


def _joinable_gap(prev: ExtractedEntity, nxt: ExtractedEntity, text: str) -> bool:
    """Two PERSON fragments belong to one name only if separated by a
    single space, or by ". " AFTER a particle/initial ("Mohd. Irfan",
    "S. Kumar"). A ". " after a complete word is a sentence break: two
    different people, not one name."""
    gap = text[prev.end_char:nxt.start_char]
    if gap == " ":
        return True
    if gap in (". ", "."):
        last = _bare(prev.text.split()[-1]) if prev.text.split() else ""
        return len(last) == 1 or last in MOHAMMAD_VARIANTS or last in HONORIFICS
    return False


def _merge_adjacent_persons(entities: list, text: str) -> list:
    """Re-join name fragments spaCy split ("Mohd" + "Irfan")."""
    ordered = sorted(entities, key=lambda e: e.start_char)
    result: list = []
    for e in ordered:
        prev = result[-1] if result else None
        if (prev is not None and prev.entity_type == EntityType.PERSON and e.entity_type == EntityType.PERSON
                and prev.metadata.get("engine") not in ("rule:alias", "rule:devanagari")
                and e.metadata.get("engine") not in ("rule:alias", "rule:devanagari")
                and _joinable_gap(prev, e, text)
                and all(t in _NAMEISH for t in _tokens_lower(prev.text + " " + e.text))):
            prev.end_char = e.end_char
            prev.text = text[prev.start_char:prev.end_char]
            prev.normalized_text = normalize_entity(prev)
            prev.metadata["correction_applied"] = "merged_split_person_name"
            prev.confidence = confidence_score(prev)
        else:
            result.append(e)
    return result


# ---------------------------------------------------------------------------
# Recovery: things spaCy missed entirely
# ---------------------------------------------------------------------------

_SECTOR_RE = re.compile(r"\b(?:Sector|Phase|Block)[-\s]?\d{1,3}[A-Z]?\b")
_SUFFIX_ALT = "|".join(sorted({s.title() if s != "isbt" else "ISBT" for s in PLACE_SUFFIXES} | {"Rd"}, key=len, reverse=True))
_PLACE_SCAN_RE = re.compile(
    r"\b((?:[A-Z][A-Za-z]+\s+){1,2}(?:" + _SUFFIX_ALT + r")\.?"
    r"(?:\s+(?:ISBT|Bus\s+Stand|Metro\s+Station|Railway\s+Station|Junction))?)\b"
)
_CUE_PLACE_RE = re.compile(
    r"(?i:\bvillage|\bvill\.?|\br/o|\bresident\s+of|\bnative\s+of|\bdistrict|\bdist\.?|\bps|\bthana)\s+"
    r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"
)
_CAP_RUN_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b")
_BUS_STAND_RE = re.compile(r"\b(?:[A-Z][a-z]+\s+)?(?i:bus\s+stand|bus\s+stop|railway\s+station|metro\s+station)\b")
_CITY_SORTED = sorted(CITIES_STATES, key=len, reverse=True)
_ORG_SCAN_RE = re.compile(
    r"\b((?:[A-Z][A-Za-z&]+\s+){1,4}(?:Pvt\.?\s+Ltd\.?|Ltd\.?|LLP|Traders|Enterprises|Trust|Associates|Services|"
    r"Agency|Consultancy|Consultants|Travels|Tours|Hotel|Lodge|Guest\s+House|Dhaba|Hospital|Foundation|Society|"
    r"Placement|Placements|Exports|Industries|Finance|Bank|Motors|Transport|Logistics))\b"
)


def _overlaps(span, claimed) -> bool:
    s, e = span
    return any(s < ce and cs < e for cs, ce in claimed)


def find_org_entities(text: str, doc_id: str) -> list:
    """Organisation names ending in a business word ("Om Job Agency",
    "Balaji Travels Pvt Ltd"). Claimed BEFORE spaCy runs: many Indian
    business names start with a personal name or deity (Om, Sai, Balaji,
    Shree), and if spaCy sees them first it splits off a bogus PERSON."""
    found = []
    for m in _ORG_SCAN_RE.finditer(text):
        words = m.group(1).split()
        while words and (words[0].lower() in _SENTENCE_STARTERS or words[0].lower() in HONORIFICS
                         or words[0].lower() in ROLE_WORDS or words[0].lower() in PLACE_CUE_WORDS):
            words.pop(0)
        # drop leading full personal names glued on by capitalisation ("Ramesh Kumar Star Job Agency")
        while len(words) > 2 and words[0].lower() in FIRST_NAMES and words[1].lower() in SURNAMES:
            words = words[2:]
        if len(words) >= 2:
            start = text.index(words[0], m.start())
            found.append(make_entity(text[start:m.end(1)], EntityType.ORGANIZATION, doc_id, start, m.end(1),
                                     engine="rule:recovered", correction_applied="org_suffix_claimed"))
    return found


def recover_gazetteer_entities(entities: list, text: str, doc_id: str) -> list:
    """Add places, orgs and people that no earlier pass claimed."""
    claimed = [(e.start_char, e.end_char) for e in entities]
    added: list = []

    def _add(start, end, etype, engine, **meta):
        if _overlaps((start, end), claimed):
            return
        ent = make_entity(text[start:end], etype, doc_id, start, end, engine=engine, **meta)
        added.append(ent)
        claimed.append((start, end))

    for m in _SECTOR_RE.finditer(text):
        _add(m.start(), m.end(), EntityType.LOCATION, "rule:gazetteer", correction_applied="recovered_sector")

    for m in _ORG_SCAN_RE.finditer(text):
        words = m.group(1).split()
        while words and words[0].lower() in _SENTENCE_STARTERS | HONORIFICS:
            words.pop(0)
        if len(words) >= 2:
            start = text.index(words[0], m.start())
            _add(start, m.end(1), EntityType.ORGANIZATION, "rule:recovered", correction_applied="recovered_org_suffix")

    for m in _PLACE_SCAN_RE.finditer(text):
        words = m.group(1).split()
        while words and (words[0].lower() in _SENTENCE_STARTERS or words[0].lower() in HONORIFICS
                         or words[0].lower() in PLACE_CUE_WORDS):
            words.pop(0)
        if len(words) < 2:
            continue
        start = text.index(words[0], m.start())
        _add(start, m.end(1), EntityType.LOCATION, "rule:gazetteer", correction_applied="recovered_place_suffix")

    for m in _BUS_STAND_RE.finditer(text):
        _add(m.start(), m.end(), EntityType.LOCATION, "rule:gazetteer", correction_applied="recovered_transit_hub")

    for city in _CITY_SORTED:
        for m in re.finditer(r"(?<![A-Za-z])" + re.escape(city) + r"(?![A-Za-z])", text, flags=re.IGNORECASE):
            if text[m.start()].isupper():
                _add(m.start(), m.end(), EntityType.LOCATION, "rule:gazetteer", correction_applied="recovered_known_place")

    for m in _CUE_PLACE_RE.finditer(text):
        words = m.group(1).split()
        if _bare(words[0]) in HONORIFICS | NON_ENTITY_ACRONYMS:
            continue
        if _bare(words[0]) in FIRST_NAMES and _bare(words[0]) not in COMMON_VILLAGE_NAMES:
            continue
        end = m.start(1) + len(words[0])
        if len(words) == 2 and _bare(words[1]) not in _NAMEISH and _bare(words[1]) not in SURNAMES:
            end = m.end(1)
        _add(m.start(1), end, EntityType.LOCATION, "rule:gazetteer", correction_applied="recovered_location_cue")

    for m in _CAP_RUN_RE.finditer(text):
        words = [(w.group(), w.start() + m.start()) for w in re.finditer(r"[A-Z][a-z]+", m.group())]
        i = 0
        while i < len(words):
            low = words[i][0].lower()
            if not fuzzy_name(low) or low in CITIES_STATES or low in COMMON_VILLAGE_NAMES or low in COMMON_WORDS \
                    or low in _TRIM_WORDS:
                i += 1
                continue
            j = i
            while j + 1 < len(words) and fuzzy_name(words[j + 1][0]) \
                    and words[j + 1][0].lower() not in CITIES_STATES and words[j + 1][0].lower() not in COMMON_WORDS:
                j += 1
            run = [w[0].lower() for w in words[i:j + 1]]
            if not _is_name_run(run):
                i = j + 1
                continue
            has_given = any(t in FIRST_NAMES or t in NICKNAMES or t in MOHAMMAD_VARIANTS for t in run)
            if all(t in MOHAMMAD_VARIANTS for t in run):
                i = j + 1
                continue
            if has_given and not (len(run) == 1 and run[0] in SURNAMES and run[0] not in FIRST_NAMES | NICKNAMES):
                start, end = words[i][1], words[j][1] + len(words[j][0])
                if len(run) == 1:
                    if run[0] in HONORIFICS or run[0] in _SENTENCE_STARTERS:
                        i = j + 1
                        continue
                _add(start, end, EntityType.PERSON, "rule:recovered", correction_applied="recovered_known_name")
            i = j + 1
    merged = _merge_transit_hubs(entities + added, text)
    return _merge_sector_city(merged, text)


# ---------------------------------------------------------------------------
# Person <-> identifier proximity (feeds multi-signal resolution)
# ---------------------------------------------------------------------------

def _sentence_breaks(text: str) -> list:
    breaks = [m.start() for m in re.finditer(r"[;\n।]", text)]
    for m in re.finditer(r"\.(?=\s+[A-Z\u0900-\u097F])", text):
        before = re.search(r"(\S+)$", text[:m.start()])
        token = before.group(1).strip("()\"',") if before else ""
        word = re.sub(r"[^a-z]", "", token.lower())
        is_number = bool(re.search(r"\d$", token))       # "...9876543210. Next sentence"
        if not is_number and (len(word) <= 1 or word in HONORIFICS or word in MOHAMMAD_VARIANTS
                              or word in {"rs", "no", "dist", "vill", "st", "ps"}):
            continue                                        # "Mohd. Irfan", "S. Kumar", "Rs. 500"
        breaks.append(m.start())
    return sorted(breaks)


def attach_nearby_identifiers(entities: list, text: str, max_distance: int = 110) -> None:
    """For every PERSON, record the phones/vehicles/accounts mentioned in
    the same sentence and closer to this person than to any other person
    (metadata["nearby_ids"], e.g. ["PHONE:9812345678"]). This is the link
    that lets "Raju S." in a CDR note and "Raju Kumar" in an FIR merge
    when both are tied to the same number. Persisted with the entity.
    """
    persons = sorted((e for e in entities if e.entity_type == EntityType.PERSON), key=lambda e: e.start_char)
    ids = [e for e in entities if e.entity_type in (EntityType.PHONE, EntityType.VEHICLE, EntityType.ACCOUNT)]
    if not persons:
        return
    breaks = _sentence_breaks(text)

    def _same_sentence(a: int, b: int) -> bool:
        lo, hi = min(a, b), max(a, b)
        return not any(lo <= br < hi for br in breaks)

    for p in persons:
        p.metadata["nearby_ids"] = []
    for ident in ids:
        centre = (ident.start_char + ident.end_char) // 2
        best, best_d = None, None
        for p in persons:
            pc = (p.start_char + p.end_char) // 2
            d = abs(pc - centre)
            if d <= max_distance and _same_sentence(pc, centre) and (best_d is None or d < best_d):
                best, best_d = p, d
        if best is not None:
            key = f"{ident.entity_type.value}:{ident.normalized_text}"
            if key not in best.metadata["nearby_ids"]:
                best.metadata["nearby_ids"].append(key)
