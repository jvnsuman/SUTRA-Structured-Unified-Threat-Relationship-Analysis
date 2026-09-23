"""
tests/test_indian_extraction.py

Regression tests for the Indian-FIR rule layer (nlp/indian_rules.py).
Every case here is a failure that was MEASURED on the original
spaCy-only pipeline (see docs/EVALUATION.md), not a hypothetical.
"""

import pytest

from nlp.extraction import EntityType, extract_entities


def _ents(text):
    return [(e.entity_type, e.text) for e in extract_entities(text, "d1")]


def _has(text, etype, needle):
    return any(t == etype and needle in s for t, s in _ents(text))


def test_alias_is_one_declared_pair_with_a_shared_group():
    ents = extract_entities("Suresh Yadav alias Sonu used phone 9812345678.", "d1")
    people = [e for e in ents if e.entity_type == EntityType.PERSON]
    assert {p.text for p in people} == {"Suresh Yadav", "Sonu"}
    assert people[0].metadata["alias_group"] == people[1].metadata["alias_group"]


@pytest.mark.parametrize("marker", ["alias", "urf", "@", "a.k.a."])
def test_alias_markers(marker):
    ents = extract_entities(f"Mohd. Irfan {marker} Guddu was arrested.", "d1")
    groups = {e.metadata.get("alias_group") for e in ents if e.entity_type == EntityType.PERSON}
    assert len(groups) == 1 and None not in groups


def test_mohd_irfan_is_not_split_into_fragments():
    texts = [s for t, s in _ents("accused Mohd. Irfan @ Guddu r/o Sector 14 Gurgaon") if t == EntityType.PERSON]
    assert "Mohd. Irfan" in texts and "Guddu" in texts and "Mohd" not in texts


def test_village_is_a_location_not_a_person():
    assert _has("Ramesh Sharma of village Rampur stated", EntityType.LOCATION, "Rampur")
    assert not _has("Ramesh Sharma of village Rampur stated", EntityType.PERSON, "Rampur")


def test_police_station_prefix_is_stripped_and_typed_as_location():
    assert _has("FIR at PS Thawe, district Gopalganj", EntityType.LOCATION, "Thawe")


def test_three_letter_vehicle_series_is_recognised_and_not_a_person():
    ents = _ents("a white Maruti Swift DL8CAF5031 was seen")
    assert (EntityType.VEHICLE, "DL8CAF5031") in ents
    assert not any(t == EntityType.PERSON for t, _ in ents)


@pytest.mark.parametrize("raw,digits", [
    ("98110-22334", "9811022334"), ("+91 9811022334", "9811022334"),
    ("987-654-3210", "9876543210"), ("09811022334", "9811022334"), ("९८११०२२३३४", "9811022334"),
])
def test_phone_variants_normalise_to_ten_digits(raw, digits):
    ents = extract_entities(f"Contacted on mobile {raw}.", "d1")
    phones = [e for e in ents if e.entity_type == EntityType.PHONE]
    assert len(phones) == 1 and phones[0].normalized_text == digits


def test_long_digit_run_with_account_cue_is_an_account_not_a_phone():
    ents = extract_entities("Money sent to a/c no 123456789012 yesterday.", "d1")
    assert [e.entity_type for e in ents if e.normalized_text == "123456789012"] == [EntityType.ACCOUNT]


def test_upi_id_is_an_account():
    assert _has("paid to rahul@okaxis for travel", EntityType.ACCOUNT, "rahul@okaxis")


def test_payment_acronym_is_not_an_organisation():
    assert not any(s.upper() == "UPI" for _, s in _ents("Deepak Verma transferred Rs 49,500 via UPI to Anil Kumar."))


def test_all_caps_names_are_extracted_with_original_text():
    ents = extract_entities("RAMESH SHARMA SEEN AT ANAND VIHAR ISBT ON MOBILE 9876501234", "d1")
    people = [e for e in ents if e.entity_type == EntityType.PERSON]
    assert [p.text for p in people] == ["RAMESH SHARMA"] and people[0].normalized_text == "Ramesh Sharma"
    assert not any(s.lower() in ("on mobile", "mobile") for _, s in _ents("RAMESH SHARMA SEEN ON MOBILE 9876501234"))


def test_devanagari_person_place_and_digits():
    ents = extract_entities("राजू कुमार को एमजी रोड पर देखा गया। मोबाइल ९८११०२२३३४", "d1")
    by_type = {e.entity_type: e for e in ents}
    assert by_type[EntityType.PERSON].normalized_text == "Raju Kumar"
    assert by_type[EntityType.LOCATION].normalized_text == "MG Road"
    assert by_type[EntityType.PHONE].normalized_text == "9811022334"


def test_honorific_and_role_words_are_not_part_of_the_name():
    people = [e.normalized_text for e in extract_entities("Complainant Smt. Sunita Devi met Recruiters Monu.", "d1")
              if e.entity_type == EntityType.PERSON]
    assert "Sunita Devi" in people and "Monu" in people


def test_misspelled_names_are_recovered_next_to_known_names():
    assert any("Suresh" in (e.normalized_text or "") or "Sursh" in e.text
               for e in extract_entities("The accused Sursh Sharma was seen.", "d1") if e.entity_type == EntityType.PERSON)


def test_sector_and_city_form_one_place():
    locs = [e.text for e in extract_entities("kept at Sector 14 Gurgaon overnight", "d1") if e.entity_type == EntityType.LOCATION]
    assert locs == ["Sector 14 Gurgaon"]


def test_transit_hub_is_one_place():
    locs = [e.text for e in extract_entities("bus left Gorakhpur Bus Stand at noon", "d1") if e.entity_type == EntityType.LOCATION]
    assert locs == ["Gorakhpur Bus Stand"]


def test_person_carries_nearby_identifiers():
    e = next(x for x in extract_entities("Raju Kumar used 9876543210. Priya Sharma has 9123456780.", "d1")
             if x.text == "Raju Kumar")
    assert e.metadata["nearby_ids"] == ["PHONE:9876543210"]


def test_entities_never_overlap_and_are_sorted():
    ents = extract_entities("Mohd. Irfan @ Guddu of village Rampur used 98110-22334 in DL8CAF5031 at Sector 14 Gurgaon", "d1")
    for a, b in zip(ents, ents[1:]):
        assert a.end_char <= b.start_char
