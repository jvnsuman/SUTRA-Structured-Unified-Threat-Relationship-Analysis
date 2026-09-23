"""
nlp/relation_rules.py

Deterministic, explainable relation extraction between entities of one
document. This is the DEFAULT relation stage: it needs no torch/
transformers, runs in milliseconds, and every edge it produces carries
the sentence that justifies it.

Why rules first: the zero-shot NLI classifier
(nlp/relation_classification.py) is O(n^2) model calls per document and
its score distribution is poorly calibrated for this task (see the
comments there). Rules exploit what FIR prose actually gives us -- the
entity *types* (a phone next to a person is a "uses/calls" link) plus
lexical cues in the same sentence ("registered to", "s/o", "transferred
Rs ... to") -- and attach each phone/vehicle/place to the NEAREST person
in the sentence instead of to every person (which is what made the
O(n^2) approach both slow and noisy).

The zero-shot model can still be layered on top for pairs the rules leave
undecided (classify_relations(use_zero_shot=True) or env
USE_ZERO_SHOT_RELATIONS=1).

Confidence values are heuristic evidence weights, not calibrated
probabilities; they are evaluated against the labelled set in
data/eval/relations.json (see docs/EVALUATION.md).
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Optional

from nlp.entities import EntityType, ExtractedEntity
from nlp.indian_rules import _sentence_breaks
from nlp.relation_classification import ClassifiedRelation, RelationType

_CALL_CUES = re.compile(r"\b(call(?:ed|s|ing)?|contact(?:ed|s|ing)?|phone|mobile|mob|number|sim|dial(?:l?ed)?|spoke|whatsapp|used|using|traced)\b|मोबाइल|फोन|नंबर", re.I)
_OWN_CUES = re.compile(r"\b(regist(?:ered|ration)|owner|owns|owned|vehicle|driving|drove|driver|car|bike|truck|bus|scooter|bearing|in\s+a|travell?ed\s+in)\b|वाहन|गाड़ी", re.I)
_PRESENT_CUES = re.compile(r"\b(near|at|seen|found|spotted|reached|arrived|left|lives?|residing|resident|r/o|village|vill|native|from|to|towards|via|crossed|outside|inside|operating|stayed|kept|confined|taken\s+to|shifted)\b|पर|में|देखा", re.I)
_ORG_CUES = re.compile(r"\b(works?|working|employee|employed|owner|owns|director|proprietor|associated|linked|placed|placement|recruit(?:ed|ing)?|agency|runs|running|manager|partner|front|company|firm)\b", re.I)
_FAMILY_ASSOC_CUES = re.compile(r"\b(s/o|d/o|w/o|c/o|son\s+of|daughter\s+of|wife\s+of|husband\s+of|brother|sister|father|mother|friend|associate|accomplice|along\s+with|accompanied|together\s+with|in\s+company\s+of|gang|aide|handler|recruiter|agent|dalal|middleman|introduced|sold|sold\s+to|handed|handed\s+over|delivered|trafficked)\b", re.I)
_MONEY_CUES = re.compile(r"\b(transfer(?:red)?|paid|pay|sent|send|received|deposit(?:ed)?|withdr[ae]w|upi|neft|rtgs|imps|rs\.?|inr|₹|rupees|amount|hawala|commission)\b", re.I)


_PRONOUN_START = re.compile(r"\s*(?:he|she|they|his|her|their|the\s+(?:accused|suspect))\b", re.I)


def _rel(a: ExtractedEntity, b: ExtractedEntity, rtype: RelationType, conf: float, sentence: str) -> ClassifiedRelation:
    return ClassifiedRelation(
        id=f"rel-{uuid.uuid4().hex[:12]}",
        entity_a_id=a.id, entity_b_id=b.id,
        relation_type=rtype, confidence=round(conf, 3),
        source_doc_id=a.source_doc_id, source_text=sentence.strip(),
    )


def _sentence_bounds(text: str) -> list:
    breaks = _sentence_breaks(text)
    bounds, prev = [], 0
    for br in breaks:
        bounds.append((prev, br + 1))
        prev = br + 1
    bounds.append((prev, len(text)))
    return bounds


def _which(bounds: list, pos: int) -> int:
    for i, (s, e) in enumerate(bounds):
        if s <= pos < e:
            return i
    return len(bounds) - 1


def _centre(e: ExtractedEntity) -> int:
    return (e.start_char + e.end_char) // 2


def classify_relations_rules(entities: list, source_text: str) -> list:
    """Rule-based relations for one document's entities. Entities must
    carry real start_char/end_char (i.e. straight from extract_entities).
    """
    if len(entities) < 2:
        return []
    bounds = _sentence_bounds(source_text)
    by_sentence: dict = {}
    for e in entities:
        by_sentence.setdefault(_which(bounds, _centre(e)), []).append(e)

    all_persons = sorted((e for e in entities if e.entity_type == EntityType.PERSON), key=_centre)

    relations: list = []
    for sidx, ents in by_sentence.items():
        s0, s1 = bounds[sidx]
        sentence = source_text[s0:s1]
        persons = sorted((e for e in ents if e.entity_type == EntityType.PERSON), key=_centre)
        if not persons and _PRONOUN_START.match(sentence):
            # "He works for Sai Placement..." -- attach to the most recent
            # person mentioned before this sentence (simple pronoun link).
            earlier = [p for p in all_persons if _centre(p) < s0]
            if earlier:
                persons = [earlier[-1]]

        def nearest_person(target: ExtractedEntity) -> Optional[ExtractedEntity]:
            if not persons:
                return None
            return min(persons, key=lambda p: abs(_centre(p) - _centre(target)))

        for e in ents:
            if e.entity_type == EntityType.PHONE:
                p = nearest_person(e)
                if p is not None:
                    cued = bool(_CALL_CUES.search(sentence))
                    relations.append(_rel(p, e, RelationType.CALLS, 0.85 if cued else 0.70, sentence))
            elif e.entity_type == EntityType.VEHICLE:
                p = nearest_person(e)
                if p is not None:
                    cued = bool(_OWN_CUES.search(sentence))
                    relations.append(_rel(p, e, RelationType.OWNS, 0.80 if cued else 0.60, sentence))
            elif e.entity_type == EntityType.LOCATION:
                p = nearest_person(e)
                if p is not None:
                    cued = bool(_PRESENT_CUES.search(sentence))
                    relations.append(_rel(p, e, RelationType.PRESENT_AT, 0.80 if cued else 0.60, sentence))
            elif e.entity_type == EntityType.ORGANIZATION:
                p = nearest_person(e)
                if p is not None:
                    cued = bool(_ORG_CUES.search(sentence))
                    relations.append(_rel(p, e, RelationType.ASSOCIATED_WITH, 0.75 if cued else 0.55, sentence))
            elif e.entity_type == EntityType.ACCOUNT:
                p = nearest_person(e)
                if p is not None:
                    paid = bool(_MONEY_CUES.search(sentence))
                    relations.append(_rel(p, e, RelationType.TRANSACTS_WITH if paid else RelationType.ASSOCIATED_WITH,
                                          0.75 if paid else 0.70, sentence))

        # person <-> person
        for i in range(len(persons)):
            for j in range(i + 1, len(persons)):
                a, b = persons[i], persons[j]
                ga, gb = (a.metadata or {}).get("alias_group"), (b.metadata or {}).get("alias_group")
                if ga and ga == gb:
                    continue  # same person, not a relationship
                between = source_text[a.end_char:b.start_char]
                window = between if len(between) <= 160 else between[:160]
                if _MONEY_CUES.search(window):
                    relations.append(_rel(a, b, RelationType.TRANSACTS_WITH, 0.80, sentence))
                elif _FAMILY_ASSOC_CUES.search(window):
                    relations.append(_rel(a, b, RelationType.ASSOCIATED_WITH, 0.80, sentence))
                elif len(between) <= 60:
                    relations.append(_rel(a, b, RelationType.ASSOCIATED_WITH, 0.55, sentence))

        # phone -> phone (CDR-style "X called Y") and account -> account
        for typ, rtype in ((EntityType.PHONE, RelationType.CALLS), (EntityType.ACCOUNT, RelationType.TRANSACTS_WITH)):
            same = sorted((e for e in ents if e.entity_type == typ), key=_centre)
            for i in range(len(same) - 1):
                a, b = same[i], same[i + 1]
                if a.normalized_text == b.normalized_text:
                    continue
                between = source_text[a.end_char:b.start_char]
                cue = _CALL_CUES if typ == EntityType.PHONE else _MONEY_CUES
                if cue.search(between) and len(between) <= 160:
                    relations.append(_rel(a, b, rtype, 0.90, sentence))
    return relations


def classify_relations(entities: list, source_text: str, use_zero_shot: Optional[bool] = None,
                       max_zero_shot_entities: int = 12) -> list:
    """Rules first; optionally add zero-shot NLI relations for entity
    pairs the rules left undecided. Never raises for a missing
    transformers/torch install -- rules alone are a complete answer.
    """
    relations = classify_relations_rules(entities, source_text)

    if use_zero_shot is None:
        use_zero_shot = os.environ.get("USE_ZERO_SHOT_RELATIONS", "").lower() in ("1", "true", "yes")
    if not use_zero_shot or not (2 <= len(entities) <= max_zero_shot_entities):
        return relations

    try:
        from nlp import relation_classification as zs
        decided = {frozenset((r.entity_a_id, r.entity_b_id)) for r in relations}
        import itertools
        for a, b in itertools.combinations(entities, 2):
            if frozenset((a.id, b.id)) in decided:
                continue
            rel = zs.classify_relation(a, b, source_text)
            if rel.relation_type != RelationType.UNRELATED:
                relations.append(rel)
    except RuntimeError:
        pass  # transformers/torch unavailable: rules-only result stands
    return relations
