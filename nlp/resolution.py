"""
nlp/resolution.py

Stage 3: entity resolution / deduplication -- deciding that "Suresh
Yadav", "Sonu" and "सुरेश यादव" across three documents are one person, and
that "Ramesh Sharma" and "Ramesh Verma" are not.

Design
------
1. Pair scoring (multi-signal). Every candidate pair of same-type
   mentions gets a probability built from independent evidence, merged
   with a noisy-OR (nlp/confidence.combine_evidence):
       * name similarity  -- token-aligned, phonetic, script-independent
         ("Mohd."="Mohammad", "Raju"="राजू", initials, surname veto);
       * explicit alias   -- the document itself says "X alias Y" / "X @ Y";
       * declared alias   -- another document uses a nickname that a
         document declared as X's alias;
       * shared identifier-- the same phone / vehicle / account sits next
         to both names (mention-level), or appears in both documents;
       * shared context   -- the same locations/organisations in both docs.
   Negative evidence: two full names that clearly differ are a *veto*
   (never merged, however many phones they share -- a family phone is
   not identity), and disjoint identifiers dampen a same-name match.
   PHONE/VEHICLE/ACCOUNT are exact-match only (one digit apart is a
   different number).

2. Constraint-aware clustering. Pairs are merged in descending score
   order (Kruskal-style), but two clusters are only joined if NO cross
   pair is vetoed. This fixes the old greedy behaviour where "Ramesh"
   could bridge "Ramesh Sharma" and "Ramesh Verma" into one person.

3. Review, not silence. score >= 0.80 merges silently; 0.60-0.80 merges
   AND sets ResolvedEntity.needs_review with the reasons; < 0.60 does not
   merge. Investigators can override any decision (`overrides`:
   never_merge / force_merge on mention pairs), persisted per case.

4. Deterministic ids. A resolved entity's id is a hash of its member
   mention ids, so the same evidence yields the same node id across
   requests (needed for a stable evidence trail and ledger).
"""

from __future__ import annotations

import hashlib
import re
from itertools import combinations
from dataclasses import dataclass, field
from typing import Optional

try:
    from rapidfuzz import fuzz
    from rapidfuzz.distance import DamerauLevenshtein
except ImportError as exc:
    raise ImportError(
        "rapidfuzz is required for nlp/resolution.py. Install with:\n"
        "    pip install rapidfuzz --break-system-packages"
    ) from exc

from nlp.confidence import AUTO_MERGE_THRESHOLD, REVIEW_THRESHOLD, combine_evidence
from nlp.entities import EntityType, ExtractedEntity
from nlp.translit import canonical_place, name_tokens, phonetic_key

# PHONE/VEHICLE/ACCOUNT never use fuzzy matching: two numbers one digit
# apart score ~90% similarity under a string ratio, which would silently
# merge different people's records. Exact match only.
_EXACT_MATCH_TYPES = frozenset({EntityType.PHONE, EntityType.VEHICLE, EntityType.ACCOUNT})
_IDENTIFIER_TYPES = _EXACT_MATCH_TYPES

# Fuzzy threshold (0-100) for LOCATION/ORGANIZATION text similarity.
DEFAULT_SIMILARITY_THRESHOLD = 82.0

# ---- evidence weights (hand-set; validated in docs/EVALUATION.md) --------
# A full name alone is NOT enough for a silent merge: "Suresh Sharma" is as
# common in India as "John Smith" in the UK. Name-only matches land in the
# review band (0.60-0.80: merged and flagged); one corroborating signal
# (shared phone/vehicle, an alias link) lifts them above 0.80.
P_NAME_EXACT_FULL = 0.72        # same full name (2+ tokens), incl. across scripts
P_NAME_FUZZY_FULL = 0.62        # full names within transliteration/spelling noise
P_NAME_INITIAL = 0.55           # "Raju S." vs "Raju Sharma"
P_NAME_INITIAL_CONFLICT = 0.25  # "Raju S." vs "Raju Kumar": weak, needs corroboration
P_NAME_SINGLE_EQUAL = 0.40      # "Sonu" vs "Sonu": common nicknames, weak alone
P_NAME_PARTIAL = 0.40           # "Sonu" vs "Sonu Yadav"
P_ALIAS_IN_DOCUMENT = 0.97      # "X alias Y" in one document
P_ALIAS_DECLARED = 0.62         # nickname declared as X's alias elsewhere in the case
P_SAME_DOC_COREFERENCE = 0.90   # same full name twice in one document
P_SAME_DOC_PARTIAL = 0.70       # "Ramesh" / "Ramesh Sharma" in one document
P_SHARED_MENTION_ID = 0.65      # same phone/vehicle sits next to both names
P_SHARED_ACCOUNT = 0.70
# Document-level context is weak evidence: every document of ONE case tends
# to share the ring's phones, vehicles and cities, so co-occurrence in two
# documents says little about two NAMES being one person. Small weights,
# only counted when the names already agree, generic cities ignored.
P_SHARED_DOC_ID = 0.04          # same identifier somewhere in both documents (cap 1)
P_SHARED_CONTEXT = 0.03         # same specific place/org in both documents (cap 2)
MIN_NAME_P_FOR_CONTEXT = 0.55
DISJOINT_ID_DAMPING = 0.85      # both names have identifiers, none shared


@dataclass
class ResolvedEntity:
    """One canonical entity merged from one or more ExtractedEntity mentions."""

    id: str
    entity_type: EntityType
    canonical_text: str
    mention_ids: list = field(default_factory=list)
    source_doc_ids: set = field(default_factory=set)
    # True for a cross-document LOCATION/ORGANIZATION merge with no
    # corroborating PERSON/PHONE/VEHICLE shared between the same
    # documents -- guards against coincidental string matches.
    is_low_confidence_cross_merge: bool = False
    # Weakest merge used to build this cluster (1.0 for a singleton).
    merge_confidence: float = 1.0
    # Merged on evidence in the review band (0.60-0.80) or ambiguous.
    needs_review: bool = False
    merge_reasons: list = field(default_factory=list)   # human-readable evidence
    aliases: list = field(default_factory=list)         # other surface forms seen
    members: list = field(default_factory=list)         # [{mention_id, text, doc_id}]


@dataclass
class MatchDecision:
    """Outcome of comparing two mentions."""

    score: float = 0.0
    reasons: list = field(default_factory=list)
    vetoed: bool = False
    veto_reason: str = ""


# ---------------------------------------------------------------------------
# Name similarity
# ---------------------------------------------------------------------------

def _token_sim(x: str, y: str) -> float:
    """Similarity in [0,1] of two name tokens (already lower-case ASCII)."""
    if x == y:
        return 1.0
    if len(x) == 1 or len(y) == 1:  # initial vs full token
        return 0.7 if x[0] == y[0] else 0.0
    if phonetic_key(x) == phonetic_key(y):
        return 0.95
    # Damerau-Levenshtein counts a swapped pair ("Surehs"/"Suresh") as ONE edit
    ratio = max(fuzz.ratio(x, y) / 100.0, DamerauLevenshtein.normalized_similarity(x, y))
    if ratio >= 0.85 or (ratio >= 0.8 and min(len(x), len(y)) >= 5):
        return ratio
    kr = fuzz.ratio(phonetic_key(x), phonetic_key(y)) / 100.0
    if kr >= 0.9 and min(len(x), len(y)) >= 4:
        return 0.85
    return ratio * 0.6


def _token_sim_strict(x: str, y: str) -> float:
    """For bare single names ("Monu" vs "Munna"): spelling variants only.
    Phonetic equality is too loose when there is no surname to back it up."""
    if x == y:
        return 1.0
    ratio = fuzz.ratio(x, y) / 100.0
    return ratio if ratio >= 0.9 and min(len(x), len(y)) >= 4 else 0.0


def _name_probability(ta: list, tb: list) -> tuple:
    """(probability, kind, veto) for two token lists. kind is a short
    label used in the human-readable reason."""
    if not ta or not tb:
        return 0.0, "empty", False
    # "Mohammad" is a courtesy prefix in many records ("Mohd. Sanjay Singh"
    # is also written "Sanjay Singh"): ignore it when only one side has it.
    if len(ta) >= 3 and ta[0] == "mohammad" and tb[0] != "mohammad":
        ta = ta[1:]
    elif len(tb) >= 3 and tb[0] == "mohammad" and ta[0] != "mohammad":
        tb = tb[1:]
    if ta == tb and len(ta) >= 2:
        return P_NAME_EXACT_FULL, "same full name", False

    if len(ta) >= 2 and len(tb) >= 2:
        first = _token_sim(ta[0], tb[0])
        last = _token_sim(ta[-1], tb[-1])
        if first < 0.7:
            return 0.0, "different first name", True
        a_init, b_init = len(ta[-1]) == 1, len(tb[-1]) == 1
        if a_init or b_init:
            if last >= 0.7:
                return P_NAME_INITIAL, "surname initial matches", False
            return P_NAME_INITIAL_CONFLICT, "surname initial differs", False
        if last < 0.6:
            return 0.0, "different surname", True
        score = 0.5 * first + 0.5 * last
        if score >= 0.97:
            return P_NAME_EXACT_FULL, "same full name (spelling variant)", False
        if score >= 0.82:
            return P_NAME_FUZZY_FULL, "similar full name", False
        return 0.0, "names differ", False

    # at least one single-token name
    if len(ta) == 1 and len(tb) == 1:
        if _token_sim_strict(ta[0], tb[0]) >= 0.9:
            return P_NAME_SINGLE_EQUAL, "same single name", False
        return 0.0, "names differ", False
    single, multi = (ta, tb) if len(ta) == 1 else (tb, ta)
    if max(_token_sim_strict(single[0], multi[0]), _token_sim_strict(single[0], multi[-1])) >= 0.9:
        return P_NAME_PARTIAL, "partial name", False
    return 0.0, "names differ", False


# ---------------------------------------------------------------------------
# Context (cross-mention information used by pair scoring)
# ---------------------------------------------------------------------------

from nlp.gazetteer import CITIES_STATES as _CITIES

# Big cities/states appear in nearly every report of a case: not evidence.
_GENERIC_PLACES = frozenset(_CITIES)


def _place_key(text: str) -> str:
    t = canonical_place(text or "").lower()
    t = re.sub(r"[.,]", "", t)
    t = re.sub(r"\brd\b", "road", t)
    t = re.sub(r"\bext\b", "extension", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class _Context:
    doc_places: dict = field(default_factory=dict)       # doc -> {place keys}
    doc_orgs: dict = field(default_factory=dict)
    doc_identifiers: dict = field(default_factory=dict)  # doc -> {"PHONE:98..."}
    alias_of: dict = field(default_factory=dict)         # alias mention id -> primary's text
    group_primary: dict = field(default_factory=dict)    # alias_group id -> primary mention
    never: set = field(default_factory=set)              # frozenset({idA, idB})
    force: set = field(default_factory=set)


def _norm_key(e: ExtractedEntity) -> str:
    return e.normalized_text or e.text


def _build_context(entities: list, overrides: Optional[dict]) -> _Context:
    ctx = _Context()
    groups: dict = {}
    for e in entities:
        doc = e.source_doc_id
        if e.entity_type == EntityType.LOCATION:
            ctx.doc_places.setdefault(doc, set()).add(_place_key(_norm_key(e)))
        elif e.entity_type == EntityType.ORGANIZATION:
            ctx.doc_orgs.setdefault(doc, set()).add(_place_key(_norm_key(e)))
        elif e.entity_type in _IDENTIFIER_TYPES:
            ctx.doc_identifiers.setdefault(doc, set()).add(f"{e.entity_type.value}:{_norm_key(e)}")
        grp = (e.metadata or {}).get("alias_group")
        if grp and e.entity_type == EntityType.PERSON:
            groups.setdefault(grp, []).append(e)
    for members in groups.values():
        primary = next((m for m in members if (m.metadata or {}).get("alias_role") == "primary"), members[0])
        ctx.group_primary[(primary.metadata or {}).get("alias_group")] = primary
        for m in members:
            if m.id != primary.id:
                ctx.alias_of[m.id] = f"{primary.text} ({primary.source_doc_id})"
    for pair in (overrides or {}).get("never_merge", []):
        ctx.never.add(frozenset(pair))
    for pair in (overrides or {}).get("force_merge", []):
        ctx.force.add(frozenset(pair))
    return ctx


# ---------------------------------------------------------------------------
# Pair scoring
# ---------------------------------------------------------------------------

def _score_person_pair(a: ExtractedEntity, b: ExtractedEntity, ctx: _Context) -> MatchDecision:
    ma, mb = a.metadata or {}, b.metadata or {}
    same_doc = a.source_doc_id == b.source_doc_id

    if same_doc and ma.get("alias_group") and ma.get("alias_group") == mb.get("alias_group"):
        return MatchDecision(P_ALIAS_IN_DOCUMENT,
                             [f"document states they are the same person ('{a.text}' alias '{b.text}')"])

    ta, tb = name_tokens(_norm_key(a)), name_tokens(_norm_key(b))
    name_p, kind, veto = _name_probability(ta, tb)
    decision = MatchDecision()

    # The same nickname declared as the alias of two DIFFERENT people
    # ("Mahesh Pandey alias Monu" / "Gopal Pandey alias Monu") does not
    # identify anyone: the two "Monu"s are not evidence of one person.
    ga, gb = ma.get("alias_group"), mb.get("alias_group")
    if ga and gb and ga != gb and ga in ctx.group_primary and gb in ctx.group_primary:
        pa, pb = ctx.group_primary[ga], ctx.group_primary[gb]
        if _name_probability(name_tokens(_norm_key(pa)), name_tokens(_norm_key(pb)))[2]:
            decision.vetoed = True
            decision.veto_reason = f"'{a.text}' is an alias of different people ({pa.text} / {pb.text})"
            return decision

    if veto:
        decision.vetoed, decision.veto_reason = True, f"{kind}: '{a.text}' vs '{b.text}'"
        return decision

    if same_doc:
        # Two different people are routinely named in one document
        # (complainant, suspect, witness): only merge on clear coreference.
        if name_p >= P_NAME_EXACT_FULL:
            return MatchDecision(P_SAME_DOC_COREFERENCE, [f"same name twice in {a.source_doc_id}"])
        if kind == "partial name":
            return MatchDecision(P_SAME_DOC_PARTIAL, [f"partial name in the same document '{a.text}'/'{b.text}'"])
        return decision

    probs, reasons = [], []
    if name_p > 0:
        probs.append(name_p)
        reasons.append(f"{kind} ('{a.text}' / '{b.text}')")

    # A nickname that one document explicitly declares as someone's alias
    # ("Suresh Yadav alias Sonu") is stronger evidence than a bare common
    # nickname when it reappears in another document of the same case.
    declared = ctx.alias_of.get(a.id) or ctx.alias_of.get(b.id)
    if declared and kind in ("same single name", "same full name", "partial name"):
        probs.append(P_ALIAS_DECLARED)
        reasons.append(f"'{(a if a.id in ctx.alias_of else b).text}' is declared as an alias of {declared}")

    if name_p > 0 or probs:
        ids_a, ids_b = set(ma.get("nearby_ids", [])), set(mb.get("nearby_ids", []))
        shared = ids_a & ids_b
        for ident in sorted(shared):
            probs.append(P_SHARED_ACCOUNT if ident.startswith("ACCOUNT") else P_SHARED_MENTION_ID)
            reasons.append(f"both associated with {ident.split(':', 1)[0].lower()} {ident.split(':', 1)[1]}")
        if name_p >= MIN_NAME_P_FOR_CONTEXT:
            doc_shared = (ctx.doc_identifiers.get(a.source_doc_id, set())
                          & ctx.doc_identifiers.get(b.source_doc_id, set())) - shared
            for ident in sorted(doc_shared)[:1]:
                probs.append(P_SHARED_DOC_ID)
                reasons.append(f"{ident.split(':', 1)[0].lower()} {ident.split(':', 1)[1]} appears in both documents")
            for store, label in ((ctx.doc_places, "location"), (ctx.doc_orgs, "organisation")):
                common = {k for k in (store.get(a.source_doc_id, set()) & store.get(b.source_doc_id, set()))
                          if k not in _GENERIC_PLACES}
                for key in sorted(common)[:2]:
                    probs.append(P_SHARED_CONTEXT)
                    reasons.append(f"both documents mention the same {label} '{key}'")

    if not probs or name_p == 0 and not any("declared" in r for r in reasons):
        return decision

    score = combine_evidence(probs)
    if ids_a and ids_b and not shared and name_p > 0:
        score = round(score * DISJOINT_ID_DAMPING, 4)
        reasons.append("names match but they are tied to different identifiers")
    decision.score, decision.reasons = score, reasons
    return decision


def _text_similarity(a: ExtractedEntity, b: ExtractedEntity) -> float:
    """0-100 similarity of two LOCATION/ORGANIZATION mentions."""
    ka, kb = _place_key(_norm_key(a)), _place_key(_norm_key(b))
    if ka == kb:
        return 100.0
    return float(fuzz.token_sort_ratio(ka, kb))


def _place_conflict(a: ExtractedEntity, b: ExtractedEntity) -> bool:
    """Hard veto: "Sector 14" vs "Sector 15", "Block A" vs "Block B"."""
    ta, tb = _place_key(_norm_key(a)).split(), _place_key(_norm_key(b)).split()
    if len(ta) != len(tb):
        return False
    for x, y in zip(ta, tb):
        if x != y and (len(x) <= 2 or len(y) <= 2 or any(ch.isdigit() for ch in x + y)):
            return True
    return False


def _score_pair(a: ExtractedEntity, b: ExtractedEntity, ctx: _Context,
                threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> MatchDecision:
    if a.entity_type != b.entity_type:
        return MatchDecision()
    pair = frozenset((a.id, b.id))
    if pair in ctx.never:
        return MatchDecision(vetoed=True, veto_reason="investigator marked these as different")
    if pair in ctx.force:
        return MatchDecision(1.0, ["investigator confirmed these are the same"])

    if a.entity_type in _EXACT_MATCH_TYPES:
        if _norm_key(a) == _norm_key(b) and _norm_key(a):
            return MatchDecision(1.0, [f"identical {a.entity_type.value.lower()} {_norm_key(a)}"])
        return MatchDecision()

    if a.entity_type == EntityType.PERSON:
        return _score_person_pair(a, b, ctx)

    # LOCATION / ORGANIZATION
    if _place_conflict(a, b):
        return MatchDecision(vetoed=True, veto_reason=f"'{a.text}' and '{b.text}' differ in a number/letter")
    sim = _text_similarity(a, b)
    if sim >= 92:
        return MatchDecision(0.85 if sim < 100 else 0.95, [f"name similarity {sim:.0f}%"])
    if sim >= threshold:
        return MatchDecision(0.70, [f"name similarity {sim:.0f}%"])
    return MatchDecision()


# ---------------------------------------------------------------------------
# Public, backwards-compatible pairwise helpers
# ---------------------------------------------------------------------------

def compute_similarity(entity_a: ExtractedEntity, entity_b: ExtractedEntity) -> float:
    """Text/name similarity in [0, 100] between two same-type entities.
    PHONE/VEHICLE/ACCOUNT: exact match only. PERSON: token-aligned,
    phonetic name similarity. Others: fuzzy ratio.
    """
    if entity_a.normalized_text is None or entity_b.normalized_text is None:
        raise ValueError("compute_similarity requires normalized_text on both entities.")
    if entity_a.entity_type != entity_b.entity_type:
        return 0.0
    if entity_a.entity_type in _EXACT_MATCH_TYPES:
        return 100.0 if entity_a.normalized_text == entity_b.normalized_text else 0.0
    if entity_a.entity_type == EntityType.PERSON:
        p, _, veto = _name_probability(name_tokens(entity_a.normalized_text), name_tokens(entity_b.normalized_text))
        return 0.0 if veto else round(min(100.0, p / P_NAME_EXACT_FULL * 100.0), 1)
    return _text_similarity(entity_a, entity_b)


def resolve_match(entity_a: ExtractedEntity, entity_b: ExtractedEntity,
                  threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> bool:
    """True if two mentions, compared on their own (no case context),
    reach the review-or-better band."""
    decision = _score_pair(entity_a, entity_b, _build_context([entity_a, entity_b], None), threshold)
    return (not decision.vetoed) and decision.score >= REVIEW_THRESHOLD


def explain_match(entity_a: ExtractedEntity, entity_b: ExtractedEntity) -> MatchDecision:
    """Full MatchDecision (score, reasons, veto) for two mentions."""
    return _score_pair(entity_a, entity_b, _build_context([entity_a, entity_b], None))


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def _resolved_id(mention_ids: list) -> str:
    digest = hashlib.sha1("|".join(sorted(mention_ids)).encode("utf-8")).hexdigest()[:12]
    return f"resolved-{digest}"


def _person_rank(e: ExtractedEntity) -> tuple:
    toks = name_tokens(_norm_key(e))
    full = [t for t in toks if len(t) > 1]
    primary = 1 if (e.metadata or {}).get("alias_role") == "primary" else 0
    return (len(full), primary, e.confidence or 0.0, len(_norm_key(e)))


def merge_entities(entities: list, *, merge_confidence: float = 1.0, needs_review: bool = False,
                   reasons: Optional[list] = None) -> ResolvedEntity:
    """Combine mentions into one ResolvedEntity (all must share a type).
    canonical_text: for PERSON the most complete name (most full tokens,
    alias-primary preferred); otherwise the highest-confidence mention.
    """
    if not entities:
        raise ValueError("merge_entities requires at least one entity")
    entity_types = {e.entity_type for e in entities}
    if len(entity_types) > 1:
        raise ValueError(
            f"merge_entities requires all entities to share one entity_type, "
            f"got: {sorted(t.value for t in entity_types)}"
        )

    if entities[0].entity_type == EntityType.PERSON:
        best = max(entities, key=_person_rank)
    else:
        best = max(entities, key=lambda e: (e.confidence or 0.0, len(_norm_key(e))))

    canonical = best.normalized_text or best.text
    seen, aliases = set(), []
    for e in entities:
        form = e.normalized_text or e.text
        if form != canonical and form.lower() not in seen:
            seen.add(form.lower())
            aliases.append(form)

    mention_ids = [e.id for e in entities]
    return ResolvedEntity(
        id=_resolved_id(mention_ids),
        entity_type=best.entity_type,
        canonical_text=canonical,
        mention_ids=mention_ids,
        source_doc_ids={e.source_doc_id for e in entities},
        merge_confidence=merge_confidence,
        needs_review=needs_review,
        merge_reasons=list(dict.fromkeys(reasons or []))[:8],
        aliases=aliases,
        members=[{"mention_id": e.id, "text": e.text, "doc_id": e.source_doc_id} for e in entities],
    )


def _blocking_keys(e: ExtractedEntity) -> set:
    keys = set()
    meta = e.metadata or {}
    if meta.get("alias_group"):
        keys.add(f"g:{meta['alias_group']}")
    if e.entity_type == EntityType.PERSON:
        for tok in name_tokens(_norm_key(e)):
            keys.add(f"t:{phonetic_key(tok)}" if len(tok) > 1 else f"i:{tok}")
            keys.add(f"i:{tok[0]}")
        for ident in meta.get("nearby_ids", []):
            keys.add(f"n:{ident}")
    else:
        for tok in _place_key(_norm_key(e)).split():
            keys.add(f"t:{phonetic_key(tok) or tok}")
    return keys


_AMBIGUOUS_NOTES: dict = {}


def _drop_ambiguous_weak_edges(scored: list, group: list, decide) -> list:
    """A mention whose only links are WEAK (< auto-merge) and that resembles
    two or more mutually incompatible people ("Monu" declared as the alias
    of both "Mahesh Pandey" and "Gopal Pandey"; "Gopal S" vs "Gopal Sharma"
    and "Gopal Singh") must not be attached to whichever scores first. Its
    weak edges are dropped (it stays separate, flagged for review) rather
    than guessing. Strong edges (>= auto) are kept.
    """
    neighbours: dict = {}
    for score, i, j in scored:
        neighbours.setdefault(i, []).append((score, j))
        neighbours.setdefault(j, []).append((score, i))
    drop: set = set()
    for i, links in neighbours.items():
        weak = [j for score, j in links if score < AUTO_MERGE_THRESHOLD]
        if len(weak) < 2:
            continue
        conflicted = any(decide(a, b).vetoed for a, b in combinations(weak, 2))
        if conflicted:
            drop.update((min(i, j), max(i, j)) for j in weak)
            _AMBIGUOUS_NOTES[group[i].id] = (
                f"'{group[i].text}' could refer to several different people in this case; left unmerged")
    return [(sc, i, j) for sc, i, j in scored if (min(i, j), max(i, j)) not in drop]


blocking_keys = _blocking_keys  # public alias (used by api/routes/cross_case.py)


def _cluster_group(group: list, ctx: _Context, threshold: float) -> list:
    """Constraint-aware agglomerative clustering of same-type mentions.
    Returns a list of (members, min_score, needs_review, reasons)."""
    n = len(group)
    if group[0].entity_type in _EXACT_MATCH_TYPES:
        buckets: dict = {}
        for e in group:
            buckets.setdefault(_norm_key(e) or e.id, []).append(e)
        return [(m, 1.0, False, [f"identical {m[0].entity_type.value.lower()} {_norm_key(m[0])}"] if len(m) > 1 else [])
                for m in buckets.values()]

    index: dict = {}
    for i, e in enumerate(group):
        for k in _blocking_keys(e):
            index.setdefault(k, []).append(i)
    candidates = set()
    for members in index.values():
        if len(members) > 400:      # degenerate bucket (e.g. one very common initial)
            continue
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                candidates.add((members[x], members[y]))
    for i, j in ((i, j) for e in group for i, j in ()):
        pass
    for i, e in enumerate(group):   # forced merges always considered
        for j in range(i + 1, n):
            if frozenset((e.id, group[j].id)) in ctx.force:
                candidates.add((i, j))

    cache: dict = {}

    def decide(i: int, j: int) -> MatchDecision:
        key = (i, j) if i < j else (j, i)
        if key not in cache:
            cache[key] = _score_pair(group[key[0]], group[key[1]], ctx, threshold)
        return cache[key]

    scored = []
    for i, j in candidates:
        d = decide(i, j)
        if not d.vetoed and d.score >= REVIEW_THRESHOLD:
            scored.append((d.score, i, j))
    scored = _drop_ambiguous_weak_edges(scored, group, decide)
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))

    cluster_of = list(range(n))
    members = {i: {i} for i in range(n)}
    min_score = {i: 1.0 for i in range(n)}
    review = {i: False for i in range(n)}
    reasons = {i: [] for i in range(n)}

    for score, i, j in scored:
        ci, cj = cluster_of[i], cluster_of[j]
        if ci == cj:
            continue
        blocked = None
        for x in members[ci]:
            for y in members[cj]:
                if frozenset((group[x].id, group[y].id)) in ctx.force:
                    continue
                d = decide(x, y)
                if d.vetoed:
                    blocked = (x, y, d)
                    break
            if blocked:
                break
        if blocked:
            # remember the ambiguity: this mention also matches another person
            for c in (ci, cj):
                review[c] = True
                reasons[c].append(f"also resembles '{group[j if c == ci else i].text}' "
                                  f"but that would conflict ({blocked[2].veto_reason})")
            continue
        keep, drop = (ci, cj) if ci < cj else (cj, ci)
        members[keep] |= members[drop]
        for m in members[drop]:
            cluster_of[m] = keep
        min_score[keep] = min(min_score[keep], min_score[drop], score)
        review[keep] = review[keep] or review[drop] or score < AUTO_MERGE_THRESHOLD
        reasons[keep] = reasons[keep] + reasons[drop] + decide(i, j).reasons
        del members[drop], min_score[drop], review[drop], reasons[drop]

    return [([group[m] for m in sorted(members[c])], min_score[c], review[c], reasons[c]) for c in sorted(members)]


def resolve_entities(entities: list,
                     threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
                     overrides: Optional[dict] = None) -> list:
    """Resolve mentions across an entire document batch (cross-document,
    which is the point of network analysis) into ResolvedEntity objects.

    overrides: {"never_merge": [(mention_id, mention_id), ...],
                "force_merge": [(mention_id, mention_id), ...]}
    -- investigator decisions from the review workflow.
    """
    ctx = _build_context(entities, overrides)
    by_type: dict = {}
    for entity in entities:
        by_type.setdefault(entity.entity_type, []).append(entity)

    resolved: list = []
    for type_group in by_type.values():
        for members, score, review, reasons in _cluster_group(type_group, ctx, threshold):
            resolved.append(merge_entities(members, merge_confidence=score,
                                           needs_review=review, reasons=reasons))

    for entity in resolved:                       # surface ambiguity notes on the mention's entity
        for member in entity.members:
            note = _AMBIGUOUS_NOTES.pop(member["mention_id"], None)
            if note:
                entity.needs_review = True
                entity.merge_reasons.append(note)
    _AMBIGUOUS_NOTES.clear()
    _flag_uncorroborated_cross_merges(resolved)
    return resolved


def _flag_uncorroborated_cross_merges(resolved: list) -> None:
    """Sets is_low_confidence_cross_merge on cross-document LOCATION/
    ORGANIZATION entities with no PERSON/PHONE/VEHICLE shared between the
    same document pair. Mutates resolved in place."""
    corroborating_types = {EntityType.PERSON, EntityType.PHONE, EntityType.VEHICLE, EntityType.ACCOUNT}

    corroborated_doc_pairs: set = set()
    for entity in resolved:
        if entity.entity_type not in corroborating_types:
            continue
        docs = sorted(entity.source_doc_ids)
        for i in range(len(docs)):
            for j in range(i + 1, len(docs)):
                corroborated_doc_pairs.add((docs[i], docs[j]))

    for entity in resolved:
        if entity.entity_type not in (EntityType.LOCATION, EntityType.ORGANIZATION):
            continue
        if len(entity.source_doc_ids) < 2:
            continue
        docs = sorted(entity.source_doc_ids)
        pairs = [(docs[i], docs[j]) for i in range(len(docs)) for j in range(i + 1, len(docs))]
        if not any(pair in corroborated_doc_pairs for pair in pairs):
            entity.is_low_confidence_cross_merge = True
            entity.needs_review = True
            entity.merge_reasons.append("same place/organisation name in unrelated documents, no shared person or number")


def build_mention_to_resolved_map(resolved: list) -> dict:
    """Maps each raw ExtractedEntity.id to its ResolvedEntity.id (needed
    because ClassifiedRelation references raw mention IDs)."""
    mapping: dict = {}
    for entity in resolved:
        for mention_id in entity.mention_ids:
            mapping[mention_id] = entity.id
    return mapping


if __name__ == "__main__":
    from nlp.extraction import extract_entities

    docs = {
        "fir-001": "Suresh Yadav alias Sonu used phone 9812345678 near MG Road.",
        "cdr-002": "A call from Sonu was traced to number 9898989898 near MG Road.",
        "fir-003": "Complainant Priya Sharma reported Ramesh Verma, vehicle MH12AB1234.",
        "fir-004": "Ramesh Sharma was seen at Sector 14.",
    }
    all_entities = []
    for doc_id, text in docs.items():
        all_entities.extend(extract_entities(text, doc_id))
    for r in resolve_entities(all_entities):
        flag = "  [REVIEW]" if r.needs_review else ""
        print(f"{r.entity_type.value:12} {r.canonical_text!r:24} conf={r.merge_confidence:.2f} "
              f"docs={sorted(r.source_doc_ids)}{flag}")
        for reason in r.merge_reasons:
            print(f"      - {reason}")
