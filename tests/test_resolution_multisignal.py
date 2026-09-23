"""
tests/test_resolution_multisignal.py

Entity resolution: the judge-style scenarios ("two suspects are the same
person"), the safeguards against false merges, and the review workflow.
"""

import pytest

from nlp.extraction import extract_entities
from nlp.resolution import explain_match, resolve_entities


def _resolve(docs, overrides=None):
    ents = []
    for i, text in enumerate(docs):
        ents += extract_entities(text, f"d{i}")
    return ents, resolve_entities(ents, overrides=overrides)


def _people(res):
    return [r for r in res if r.entity_type.value == "PERSON"]


def _find(res, needle):
    return next(r for r in _people(res) if any(needle.lower() in m["text"].lower() for m in r.members))


def test_alias_in_one_document_merges_silently():
    _, res = _resolve(["Suresh Yadav alias Sonu was seen near MG Road."])
    r = _find(res, "Sonu")
    assert {"Suresh Yadav", "Sonu"} <= {m["text"] for m in r.members} and not r.needs_review
    assert r.canonical_text == "Suresh Yadav"


def test_judge_scenario_one_person_two_phones_and_a_nickname():
    """A judge's stress test: two suspects are really one person using two
    phones and a nickname."""
    ents, res = _resolve([
        "Suresh Yadav, alias Sonu, used phone 9812345678 near MG Road.",
        "A call from Sonu was traced to number 9898989898 near MG Road.",
    ])
    r = _find(res, "Suresh Yadav")
    assert len(r.members) == 3
    assert r.needs_review, "a nickname-only link must be flagged, not silent"
    assert any("alias" in reason for reason in r.merge_reasons)
    phones = {p.canonical_text for p in res if p.entity_type.value == "PHONE"}
    assert phones == {"9812345678", "9898989898"}     # numbers stay separate entities


def test_same_phone_links_name_variants_and_is_explained():
    _, res = _resolve(["Raju Kumar was seen at MG Road, phone 987-654-3210.",
                       "A call from Raju S. was traced to number 987-654-3210."])
    r = _find(res, "Raju Kumar")
    assert len(r.members) == 2 and any("phone 9876543210" in x for x in r.merge_reasons)


def test_devanagari_and_roman_name_resolve_together():
    _, res = _resolve(["राजू कुमार को एमजी रोड पर देखा गया।", "Raju Kumar was seen at MG Road."])
    assert len(_find(res, "Raju Kumar").members) == 2


def test_mohd_and_mohammad_are_the_same_name():
    _, res = _resolve(["Mohd. Irfan contacted 9811022334.", "Mohammad Irfan was arrested with 9811022334."])
    r = _find(res, "Irfan")
    assert len(r.members) == 2 and not r.needs_review    # shared phone corroborates


def test_full_name_alone_merges_but_is_flagged_for_review():
    _, res = _resolve(["Suresh Sharma reported a theft.", "Suresh Sharma was seen at the station."])
    r = _find(res, "Suresh Sharma")
    assert len(r.members) == 2 and r.needs_review and 0.6 <= r.merge_confidence < 0.8


def test_different_surname_never_merges():
    _, res = _resolve(["Ramesh Sharma reported theft.", "Ramesh Verma was a witness."])
    assert len(_people(res)) == 2


def test_different_first_name_never_merges_even_with_a_shared_phone():
    _, res = _resolve(["Priya Sharma called 9123456780.", "Ramesh Sharma called 9123456780."])
    assert len(_people(res)) == 2


def test_bare_first_name_does_not_bridge_two_different_people():
    _, res = _resolve(["Ramesh Sharma at Sector 14.", "Ramesh Verma at Sector 14.", "Ramesh was seen at Sector 14."])
    assert len(_people(res)) == 3      # never one merged "Ramesh"


def test_phone_numbers_one_digit_apart_are_different_entities():
    _, res = _resolve(["Raju Kumar 9876543210.", "Raju Kumar 9876543211."])
    assert len([r for r in res if r.entity_type.value == "PHONE"]) == 2


def test_phone_with_different_formatting_is_one_entity():
    _, res = _resolve(["Raju used 987-654-3210.", "Raju used +91 9876543210."])
    assert len([r for r in res if r.entity_type.value == "PHONE"]) == 1


def test_sector_14_and_sector_15_stay_apart():
    _, res = _resolve(["Seen at Sector 14.", "Seen at Sector 15."])
    assert len([r for r in res if r.entity_type.value == "LOCATION"]) == 2


def test_nickname_shared_by_two_different_people_is_not_guessed():
    _, res = _resolve(["Mahesh Pandey alias Monu.", "Gopal Pandey alias Monu.", "Monu was seen at the bus stand."])
    bare = next(r for r in _people(res) if len(r.members) == 1 and r.members[0]["text"] == "Monu")
    assert bare.needs_review and any("several different people" in x for x in bare.merge_reasons)


def test_resolved_ids_are_deterministic_across_runs():
    docs = ["Suresh Yadav alias Sonu used 9812345678.", "Sonu was seen again."]
    ents = []
    for i, t in enumerate(docs):
        ents += extract_entities(t, f"d{i}")
    assert [r.id for r in resolve_entities(ents)] == [r.id for r in resolve_entities(list(reversed(ents)))] or \
        {r.id for r in resolve_entities(ents)} == {r.id for r in resolve_entities(list(reversed(ents)))}


def test_never_merge_override_splits_a_merge():
    ents, res = _resolve(["Suresh Sharma reported a theft.", "Suresh Sharma was seen at the station."])
    a, b = [m["mention_id"] for m in _find(res, "Suresh Sharma").members]
    split = resolve_entities(ents, overrides={"never_merge": [(a, b)]})
    assert len(_people(split)) == 2


def test_force_merge_override_joins_what_the_scorer_kept_apart():
    ents, res = _resolve(["Raju Kumar was seen.", "Sonu was seen."])
    assert len(_people(res)) == 2
    a, b = [_find(res, n).members[0]["mention_id"] for n in ("Raju Kumar", "Sonu")]
    forced = resolve_entities(ents, overrides={"force_merge": [(a, b)]})
    assert len(_people(forced)) == 1


def test_explain_match_returns_reasons():
    ents, _ = _resolve(["Raju Kumar 9876543210.", "Raju Kumar 9876543210."])
    people = [e for e in ents if e.entity_type.value == "PERSON"]
    d = explain_match(people[0], people[1])
    assert d.score >= 0.6 and d.reasons and not d.vetoed


def test_uncorroborated_cross_document_place_match_is_flagged():
    _, res = _resolve(["A bus left MG Road at noon.", "A shop is located at MG Road."])
    loc = next(r for r in res if r.entity_type.value == "LOCATION")
    assert loc.is_low_confidence_cross_merge and loc.needs_review
