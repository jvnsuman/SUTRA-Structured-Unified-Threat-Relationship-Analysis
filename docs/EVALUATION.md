# Evaluation: how good is it, and how do we know?

Reproduce every number here with one command:

```bash
python -m scripts.evaluate --seeds 20          # both sets + a 20-ring aggregate
python -m scripts.evaluate --errors 20         # also list the first extraction errors
```

`tests/test_evaluation_regression.py` fails the build if these numbers regress.

## What is measured

| Stage | Metric |
|---|---|
| Extraction | Precision / recall / F1 per entity type. A prediction is correct if it has the **same type** and overlaps the gold span with **IoU ≥ 0.5**. |
| Resolution | Over mentions extraction found correctly (so it isolates resolution): **pairwise** precision / recall of "same real-world entity" links; number of **false merges**, and how many of those were **not flagged for review** (the dangerous kind); how many multi-mention entities came out as exactly one node. |
| Relations | Precision / recall of `(document, entity A, entity B, type)` triples, scored at entity level. |

## The data, and its limits (read this before quoting a number)

* **`labelled` — 20 hand-written FIR-style documents** (`data/eval/labelled_firs.json`, 77 gold mentions, 45 gold relations). Realistic Indian police-report style: honorifics, `alias` / `@` / `urf`, `r/o`, `Mohd.` variants, ALL-CAPS shouting, 5-5 phone grouping, `+91`, three-letter plate series, UPI ids, Hindi/Devanagari with Devanagari digits. **This was the development set**: the rules were built while looking at it, so treat it as an upper bound, not an unbiased estimate.
* **`generated` — 20 independently generated trafficking rings** (`data/trafficking_scenario.py`, seeds 1–20, 220 documents, 1,883 gold mentions). Each ring plants a handler with a nickname and three SIMs, recruiters, a transporter, a front organisation and a safehouse, and writes them differently in every document (typos, initials, `Md.`/`Mohd.`, ALL-CAPS, Hindi). These seeds were **not** used to tune the rules for extraction, but the generator's sentence templates are mine, so they are more regular than real FIRs. **Expect real-world numbers to be lower.** Distinct people get distinct first names in the generator: two different people with the same name are unresolvable from text alone, and the resolver flags such merges for review instead of guessing.
* No real police data was used. None can be, in a hackathon.

## Results

### Extraction (micro-average F1)

| Set | Original code | Now |
|---|---|---|
| labelled (hand-written) | **0.584** (P 0.667, R 0.519) | **0.994** (P 0.987, R 1.000) |
| generated, 20 rings | **0.582** (P 0.660, R 0.521) | **0.999** (P 0.999, R 0.998) |

Per type on the 20 generated rings (original → now, F1): PERSON 0.580 → 0.997, LOCATION 0.529 → 1.000, ORGANIZATION 0.206 → 0.988, PHONE 0.848 → 1.000, VEHICLE 0.884 → 1.000.

What was wrong in the original, measured on the labelled set: 14 type confusions (villages tagged PERSON, nicknames tagged LOCATION, `ISBT`/`UPI` tagged ORG), ALL-CAPS names missed entirely, three-letter plates missed, Devanagari names not extracted at all, Devanagari-digit phones not extracted, account numbers not extracted.

### Entity resolution

| Set | | Original code | Now |
|---|---|---|---|
| labelled | pairwise P / R | 1.000 / 0.667 (on only 12 findable pairs) | **1.000 / 1.000** |
| labelled | entities fully resolved to one node | 7 / 10 | **16 / 16** |
| generated, 20 rings | pairwise P / R | 0.998 / 0.837 | **1.000 / 0.968** |
| generated, 20 rings | false-merged entities | 1 (unflagged) | **0** |
| generated, 20 rings | multi-mention entities resolved to exactly one node | 180 / 221 | **396 / 423** |

The original recall looks better than it is: it was scored on far fewer correctly extracted mentions, i.e. an easier subset. The 139 entities the new resolver **flagged for review** are merges made on evidence in the 0.60–0.80 band, shown to the investigator with their reasons.

### Relations (rule-based, labelled set)

Precision 0.978, recall 0.978 (44 of 45 gold relations found, 1 spurious). The original pipeline needed a ~1.6 GB NLI model and made O(n²) calls per document; the rules need no model. Not measured: relations on the generated rings (no gold triples for them).

## Known failure modes (what still goes wrong)

* **Four-letter typos of names** ("Viay" for "Vijay", "Imrna" for "Imran"): too short to fuzzy-match safely against a name list without turning ordinary words into people. 4 of 711 planted person mentions on the generated rings.
* **Two different people with the same full name** in one case are merged and **flagged for review** (never silently). Text alone cannot tell them apart; a phone number that differs between the two lowers the score but does not veto.
* **Names outside the gazetteer** (`nlp/gazetteer.py`) rely on spaCy. spaCy's small English model is weak on Indian names; recall for unusual names will be lower than on the sets above. The gazetteers are short, auditable lists meant to be extended.
* **Hindi coverage is rule-based**: a Devanagari name is found only if its parts are in the Devanagari gazetteer. There is no statistical Hindi/regional-script NER (no model with a verified licence has been adopted; see `THIRD_PARTY_NOTICES.md`).
* **Pronoun linking is one rule** ("He works for ..." attaches to the most recent person). Longer coreference chains are not resolved.
* **Rule weights are hand-set**, not learned (`nlp/resolution.py`, `nlp/confidence.py`). They were checked against the sets above; they are not calibrated probabilities.

## Ethics note

These metrics measure whether the system links records correctly. They do **not** measure, and the system does not attempt, prediction of who will offend. Merge confidence and "needs review" flags exist so an investigator, not the algorithm, makes the final call, and every merge shows its evidence.
