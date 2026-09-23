import pytest

from nlp.translit import canonical_place, devanagari_to_roman, has_devanagari, name_tokens, phonetic_key


@pytest.mark.parametrize("dev,roman", [
    ("राजू कुमार", "raju kumar"), ("रमेश शर्मा", "ramesh sharma"), ("गुड्डू", "guddu"),
    ("सुरेश यादव", "suresh yadav"), ("अनिल", "anil"), ("मोहम्मद", "mohammad"),
])
def test_devanagari_names_transliterate(dev, roman):
    assert devanagari_to_roman(dev) == roman


@pytest.mark.parametrize("a,b", [
    ("Raju", "Rajoo"), ("Suresh", "Sursh"), ("Mohd.", "Mohammad"), ("Kumar", "Kumaar"), ("Raju", "राजू"),
])
def test_phonetic_key_collapses_spelling_variants(a, b):
    assert phonetic_key(a) == phonetic_key(b)


@pytest.mark.parametrize("a,b", [("Ramesh", "Rajesh"), ("Sharma", "Verma"), ("Anil", "Sunil")])
def test_phonetic_key_keeps_different_names_apart(a, b):
    assert phonetic_key(a) != phonetic_key(b)


def test_name_tokens_strip_honorifics_and_canonicalise_mohammad():
    assert name_tokens("Smt. Sunita Devi") == ["sunita", "devi"]
    assert name_tokens("Md. Irfan") == ["mohammad", "irfan"]
    assert name_tokens("राजू कुमार") == ["raju", "kumar"]
    assert name_tokens("Raju S.") == ["raju", "s"]


def test_canonical_place_maps_to_english():
    assert canonical_place("एमजी रोड") == "MG Road" and canonical_place("आनंद विहार") == "Anand Vihar"
    assert canonical_place("MG Road") == "MG Road" and has_devanagari("दिल्ली") and not has_devanagari("Delhi")
