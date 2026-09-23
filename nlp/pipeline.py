"""
nlp/pipeline.py

The ONE place a document becomes entities + relations. Both
api/routes/ingestion.py and scripts/populate_demo_case.py call
process_document(), so the API and the seed scripts can never drift
apart again (they used to carry two hand-copied versions of this logic).

Order: structured parse (CDR/financial rows) -> prose extraction of
raw_text -> de-duplicate identical identifiers within the document ->
rule-based relations over the prose entities -> optional zero-shot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from nlp import extraction, relation_rules, structured as structured_parser
from nlp.entities import EntityType


@dataclass
class PipelineResult:
    entities: list = field(default_factory=list)
    relations: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def process_document(document, use_zero_shot: Optional[bool] = None) -> PipelineResult:
    """document: schema.entities.SourceDocument (duck-typed: .id,
    .document_type, .raw_text, .structured).

    Raises RuntimeError only when spaCy/its model is unavailable (callers
    already handle that by storing the document without extraction).
    """
    result = PipelineResult()
    structured_entities: list = []
    structured_relations: list = []

    if getattr(document, "structured", None) and document.document_type in ("cdr", "financial"):
        structured_entities, structured_relations, warns = structured_parser.parse_structured(
            document.id, document.document_type, document.structured)
        result.warnings.extend(warns)

    text_entities = extraction.extract_entities(document.raw_text, document.id)

    # A structured record and its rendered summary text name the same
    # numbers: keep one mention per (type, normalised value) and point any
    # relation at the surviving mention.
    keep = {(e.entity_type, e.normalized_text): e for e in structured_entities
            if e.entity_type in (EntityType.PHONE, EntityType.ACCOUNT)}
    remap: dict = {}
    surviving_text_entities = []
    for e in text_entities:
        k = (e.entity_type, e.normalized_text)
        if k in keep:
            remap[e.id] = keep[k].id
        else:
            surviving_text_entities.append(e)

    text_relations = relation_rules.classify_relations(text_entities, document.raw_text, use_zero_shot=use_zero_shot)
    for r in text_relations:
        r.entity_a_id = remap.get(r.entity_a_id, r.entity_a_id)
        r.entity_b_id = remap.get(r.entity_b_id, r.entity_b_id)

    already = {(r.entity_a_id, r.entity_b_id, r.relation_type) for r in structured_relations}
    text_relations = [r for r in text_relations
                      if r.entity_a_id != r.entity_b_id
                      and (r.entity_a_id, r.entity_b_id, r.relation_type) not in already]

    result.entities = structured_entities + surviving_text_entities
    result.relations = structured_relations + text_relations
    return result
