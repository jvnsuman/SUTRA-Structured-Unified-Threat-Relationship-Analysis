"""Structured CDR/financial parsing, rule-based relations, and the shared pipeline."""

from types import SimpleNamespace as NS

from nlp.entities import EntityType
from nlp.extraction import extract_entities
from nlp.pipeline import process_document
from nlp.relation_classification import RelationType
from nlp.relation_rules import classify_relations, classify_relations_rules
from nlp.structured import parse_structured


def _rels(text):
    ents = {e.id: e for e in extract_entities(text, "d1")}
    return [(ents[r.entity_a_id].text, r.relation_type, ents[r.entity_b_id].text)
            for r in classify_relations_rules(list(ents.values()), text)]


# ---- rule-based relations -------------------------------------------------

def test_person_phone_vehicle_place_relations():
    r = _rels("Ramesh Sharma used phone 9876543210 and drove a Bolero UP32AB4567 to Anand Vihar.")
    assert ("Ramesh Sharma", RelationType.CALLS, "9876543210") in r
    assert ("Ramesh Sharma", RelationType.OWNS, "UP32AB4567") in r
    assert ("Ramesh Sharma", RelationType.PRESENT_AT, "Anand Vihar") in r


def test_phone_attaches_to_the_nearest_person_not_every_person():
    r = _rels("Raju Kumar met Priya Sharma. Priya Sharma called from 9123456780.")
    calls = [x for x in r if x[1] == RelationType.CALLS]
    assert calls == [("Priya Sharma", RelationType.CALLS, "9123456780")]


def test_money_cue_between_people_is_a_transaction():
    assert ("Deepak Verma", RelationType.TRANSACTS_WITH, "Anil Kumar") in \
        _rels("Deepak Verma transferred Rs 49,500 via UPI to Anil Kumar.")


def test_pronoun_sentence_links_to_previous_person():
    r = _rels("Suresh Yadav was arrested at Kashmere Gate ISBT. He works for Sai Placement Services Pvt Ltd.")
    assert ("Suresh Yadav", RelationType.ASSOCIATED_WITH, "Sai Placement Services Pvt Ltd") in r


def test_alias_pair_is_not_a_relationship():
    r = _rels("Suresh Yadav alias Sonu was seen.")
    assert not any(x[0] == "Suresh Yadav" and x[2] == "Sonu" for x in r)


def test_relations_work_without_torch():
    text = "Raju Kumar used 9876543210."
    ents = extract_entities(text, "d1")
    assert classify_relations(ents, text, use_zero_shot=False)


# ---- structured records ---------------------------------------------------

def test_cdr_rows_become_phone_entities_and_one_edge_per_pair():
    entities, relations, warnings = parse_structured("cdr1", "cdr", {"calls": [
        {"caller": "98110-22334", "callee": "9898989898", "timestamp": "2026-08-01T10:00:00", "duration_seconds": 40},
        {"caller": "9811022334", "callee": "9898989898", "timestamp": "2026-08-01T10:05:00", "duration_seconds": 20},
    ]})
    assert sorted(e.normalized_text for e in entities) == ["9811022334", "9898989898"]
    assert len(relations) == 1 and relations[0].relation_type == RelationType.CALLS
    assert "2 time(s)" in relations[0].source_text and warnings == []


def test_bad_cdr_rows_are_skipped_and_reported_not_fatal():
    entities, relations, warnings = parse_structured("c", "cdr", {"calls": [
        {"caller": "12345", "callee": "9898989898"}, {"caller": "9811022334", "callee": "9898989898"}]})
    assert len(relations) == 1 and len(warnings) == 1


def test_financial_record_becomes_account_edge_with_totals():
    entities, relations, _ = parse_structured("f1", "financial", {
        "sender_account": "111122223333", "receiver_account": "444455556666",
        "transactions": [{"amount": 49000, "currency": "INR"}, {"amount": 48000, "currency": "INR"}]})
    assert {e.entity_type for e in entities} == {EntityType.ACCOUNT}
    assert relations[0].relation_type == RelationType.TRANSACTS_WITH and "97,000" in relations[0].source_text


def test_subscriber_names_link_person_to_number():
    entities, relations, _ = parse_structured("c", "cdr", {
        "calls": [{"caller": "9811022334", "callee": "9898989898", "timestamp": "2026-08-01T10:00:00"}],
        "subscribers": {"9811022334": "Suresh Yadav"}})
    person = next(e for e in entities if e.entity_type == EntityType.PERSON)
    assert person.normalized_text == "Suresh Yadav" and person.metadata["nearby_ids"] == ["PHONE:9811022334"]


# ---- pipeline -------------------------------------------------------------

def test_pipeline_does_not_double_count_a_number_named_in_both_structure_and_text():
    doc = NS(id="cdr1", document_type="cdr", raw_text="Call record: 9812345678 called 9898989898.",
             structured={"calls": [{"caller": "9812345678", "callee": "9898989898", "timestamp": "2026-08-01T10:00:00"}]})
    res = process_document(doc)
    assert len([e for e in res.entities if e.entity_type == EntityType.PHONE]) == 2
    assert len([r for r in res.relations if r.relation_type == RelationType.CALLS]) == 1
    ids = {e.id for e in res.entities}
    assert all(r.entity_a_id in ids and r.entity_b_id in ids for r in res.relations)


def test_pipeline_plain_fir():
    doc = NS(id="f1", document_type="fir", structured={}, raw_text="Raju Kumar was seen near MG Road, phone 9876543210.")
    res = process_document(doc)
    assert len(res.entities) == 3 and len(res.relations) == 2
