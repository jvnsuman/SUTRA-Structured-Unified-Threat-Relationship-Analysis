"""
nlp/translit.py

Cross-script name handling with no model and no network dependency:

  * devanagari_to_roman(): rule-based Devanagari -> Latin transliteration
    tuned for personal names ("राजू कुमार" -> "raju kumar").
  * phonetic_key(): a coarse, Indian-name-aware phonetic key so that
    "Raju"/"Rajoo"/"राजू", "Mohd."/"Mohammad", "Suresh"/"Sursh" and
    "Kumar"/"Kumaar" collapse to comparable keys.
  * name_tokens(): honorific stripping + variant canonicalisation, the
    single place both extraction and resolution get comparable tokens.
  * canonical_place(): Devanagari place -> English canonical form so
    "एमजी रोड" and "MG Road" resolve to one node.

This is a heuristic, not a linguistically complete transliterator (no
medial schwa-deletion model). phonetic_key() deliberately drops most
vowels so schwa differences ("kamala"/"kamla") do not matter.
"""

from __future__ import annotations

import re
import unicodedata

from nlp.gazetteer import HONORIFICS, MOHAMMAD_VARIANTS

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def has_devanagari(text: str) -> bool:
    """True if text contains any Devanagari character."""
    return bool(_DEVANAGARI_RE.search(text or ""))


# --------------------------------------------------------------------------
# Devanagari -> Latin
# --------------------------------------------------------------------------

_INDEP_VOWELS = {
    "अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ri",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o",
}
_MATRAS = {
    "ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "े": "e",
    "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e",
}
_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "ळ": "l", "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f",
}
_VIRAMA = "\u094D"
_NUKTA = "\u093C"
_ANUSVARA = {"ं": "n", "ँ": "n"}
_NUKTA_COMBOS = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f"}


def devanagari_to_roman(text: str) -> str:
    """Transliterate Devanagari words to lower-case Latin; non-Devanagari
    characters pass through unchanged. Word-final inherent 'a' is dropped
    (राजू -> raju, कुमार -> kumar, शर्मा -> sharma, रमेश -> ramesh).
    """
    text = unicodedata.normalize("NFC", text or "")
    out_words = []
    for word in re.split(r"(\s+)", text):
        if not word or word.isspace() or not has_devanagari(word):
            out_words.append(word)
            continue
        out_words.append(_translit_word(word))
    return "".join(out_words)


def _translit_word(word: str) -> str:
    chars = list(word)
    out = []
    i = 0
    n = len(chars)
    while i < n:
        c = chars[i]
        if c in _INDEP_VOWELS:
            out.append(_INDEP_VOWELS[c])
            i += 1
        elif c in _CONSONANTS or c in _NUKTA_COMBOS:
            base = _CONSONANTS.get(c, _NUKTA_COMBOS.get(c, ""))
            if i + 1 < n and chars[i + 1] == _NUKTA and c in _NUKTA_COMBOS:
                base = _NUKTA_COMBOS[c]
                i += 1
            nxt = chars[i + 1] if i + 1 < n else ""
            if nxt == _VIRAMA:
                out.append(base)
                i += 2
            elif nxt in _MATRAS:
                out.append(base + _MATRAS[nxt])
                i += 2
            else:
                # inherent 'a', dropped at word end
                is_last = (i + 1 >= n) or all(ch in _ANUSVARA for ch in chars[i + 1:])
                out.append(base if is_last else base + "a")
                i += 1
        elif c in _ANUSVARA:
            out.append(_ANUSVARA[c])
            i += 1
        elif c in _MATRAS:
            out.append(_MATRAS[c])
            i += 1
        elif c in (_VIRAMA, _NUKTA):
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


# --------------------------------------------------------------------------
# Places: Devanagari -> canonical English
# --------------------------------------------------------------------------

_DEVANAGARI_PLACE_MAP = {
    "दिल्ली": "Delhi", "नई दिल्ली": "New Delhi", "नोएडा": "Noida", "गुड़गांव": "Gurugram",
    "गुरुग्राम": "Gurugram", "फरीदाबाद": "Faridabad", "गाज़ियाबाद": "Ghaziabad",
    "गाजियाबाद": "Ghaziabad", "मुंबई": "Mumbai", "पुणे": "Pune", "कोलकाता": "Kolkata",
    "चेन्नई": "Chennai", "बेंगलुरु": "Bengaluru", "हैदराबाद": "Hyderabad", "जयपुर": "Jaipur",
    "लखनऊ": "Lucknow", "कानपुर": "Kanpur", "वाराणसी": "Varanasi", "आगरा": "Agra",
    "पटना": "Patna", "गोपालगंज": "Gopalganj", "रांची": "Ranchi", "भोपाल": "Bhopal",
    "इंदौर": "Indore", "रामपुर": "Rampur", "सोनौली": "Sonauli", "रक्सौल": "Raxaul",
    "आनंद विहार": "Anand Vihar", "एमजी रोड": "MG Road", "एम जी रोड": "MG Road",
    "बस स्टैंड": "Bus Stand", "रेलवे स्टेशन": "Railway Station", "मंदिर मार्ग": "Mandir Marg",
}
_SUFFIX_MAP = {
    "रोड": "Road", "नगर": "Nagar", "कॉलोनी": "Colony", "चौक": "Chowk", "मार्ग": "Marg",
    "विहार": "Vihar", "पुर": "Pur", "गंज": "Ganj", "बाज़ार": "Bazar", "बाजार": "Bazar",
    "स्टेशन": "Station", "गाँव": "Village", "गांव": "Village", "मोहल्ला": "Mohalla",
    "बस्ती": "Basti", "मंडी": "Mandi",
}


def canonical_place(text: str) -> str:
    """Devanagari place -> canonical English; non-Devanagari returned as-is.
    Unknown Devanagari places fall back to transliteration, Title Case.
    """
    text = unicodedata.normalize("NFC", (text or "").strip())
    if not has_devanagari(text):
        return text
    if text in _DEVANAGARI_PLACE_MAP:
        return _DEVANAGARI_PLACE_MAP[text]
    parts = text.split()
    if len(parts) >= 2 and parts[-1] in _SUFFIX_MAP:
        head = " ".join(parts[:-1])
        head_en = _DEVANAGARI_PLACE_MAP.get(head) or devanagari_to_roman(head).title()
        return f"{head_en} {_SUFFIX_MAP[parts[-1]]}"
    return devanagari_to_roman(text).title()


# --------------------------------------------------------------------------
# Phonetic keys and name tokens
# --------------------------------------------------------------------------

_DIGRAPHS = (
    ("chh", "C"), ("ch", "C"), ("sh", "S"), ("kh", "k"), ("gh", "g"), ("jh", "j"),
    ("th", "t"), ("dh", "d"), ("bh", "b"), ("ph", "f"), ("ck", "k"), ("ee", "i"), ("ii", "i"),
    ("oo", "u"), ("uu", "u"), ("aa", "a"), ("ai", "e"), ("ay", "e"), ("ei", "e"),
    ("au", "o"), ("ou", "o"), ("aw", "o"), ("ow", "o"),
)
_SINGLE_MAP = str.maketrans({"w": "v", "z": "j", "q": "k", "c": "k", "x": "k", "y": "i"})
_VOWELS = set("aeiou")


def _ascii_fold(text: str) -> str:
    text = devanagari_to_roman(text)
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def phonetic_key(token: str) -> str:
    """Coarse phonetic key for ONE name token. Keeps a leading vowel,
    drops other vowels, collapses doubled letters. "Raju"/"Rajoo"/"राजू"
    -> "rj"; "Suresh"/"Sursh" -> "srS"; "Mohd"/"Mohammad" -> "mhmd"/"mhmd".
    """
    t = _ascii_fold((token or "").lower())
    t = re.sub(r"[^a-z]", "", t)
    if not t:
        return ""
    if t in MOHAMMAD_VARIANTS:
        return "mhmd"
    for src, dst in _DIGRAPHS:
        t = t.replace(src, dst)
    t = t.translate(_SINGLE_MAP)
    t = re.sub(r"(.)\1+", r"\1", t)
    lead = t[0] if t[0] in _VOWELS else ""
    body = "".join(ch for ch in (t[1:] if lead else t) if ch not in _VOWELS)
    return (lead + body) or t


_TOKEN_SPLIT = re.compile(r"[\s,;/]+")


def name_tokens(name: str) -> list:
    """Comparable tokens for a personal name: transliterated, punctuation
    stripped, honorifics removed, Mohd/Md/Mohammed collapsed to
    "mohammad". Single-letter initials are kept (e.g. "s" for "S.").
    """
    folded = _ascii_fold(name or "").lower()
    folded = folded.replace("@", " ").replace(".", " ")
    tokens = []
    for raw in _TOKEN_SPLIT.split(folded):
        tok = re.sub(r"[^a-z]", "", raw)
        if not tok or tok in HONORIFICS:
            continue
        if tok in MOHAMMAD_VARIANTS:
            tok = "mohammad"
        tokens.append(tok)
    return tokens
