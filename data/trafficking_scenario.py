"""
data/trafficking_scenario.py

A fully fictional, structurally realistic human-trafficking network, with
GROUND TRUTH, for the Women Safety Division angle of SUTRA (SIH26189).

Why this exists: generic random FIRs (data/generate_synthetic.py) contain
no network structure, so nothing meaningful can be *discovered* in them.
This generator plants the structure documented for trafficking rings --
a recruitment funnel, a transporter, a safehouse behind a front
organisation, and commission payments kept under a reporting threshold --
and then writes the paper trail an investigator would actually receive:

    * a HANDLER (kingpin) who uses a nickname, several SIMs and a vehicle,
      and is written down differently in every document (full name,
      alias, "Mohd."/"Md." variants, initials, ALL-CAPS, typos, Hindi);
    * RECRUITERS in source villages, each calling the handler
      (hub-and-spoke) in bursts before each transport;
    * a TRANSPORTER with a vehicle moving people source -> transit hub
      (ISBT) -> safehouse;
    * a SAFEHOUSE / front organisation ("... Placement Services Pvt Ltd");
    * commission payments from the handler to recruiters, each just under
      Rs 50,000 (structuring);
    * FIRs from complainant families in several districts, a surveillance
      report and an intelligence note, some in Hindi.

No victim is named or described: complainants refer to "her daughter",
and no real person, number, plate or organisation is used. Every value is
random/fictional.

Ground truth: every generated mention is recorded (doc, character span,
type, cluster id) so nlp extraction and entity resolution can be scored
(scripts/evaluate.py -> docs/EVALUATION.md), and the planted network
structure is recorded so graph analytics can be checked against it (the
handler must come out as the top influencer; the structuring and burst
alerts must fire).
"""

from __future__ import annotations

import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from data.generate_synthetic import DocumentType, SyntheticDocument

# Names that exist in BOTH the Latin lists and the Devanagari gazetteer,
# so a Hindi FIR can name the same person as an English one.
_HINDI_NAMES = {
    "raju": "राजू", "ramesh": "रमेश", "suresh": "सुरेश", "mahesh": "महेश", "dinesh": "दिनेश",
    "mukesh": "मुकेश", "anil": "अनिल", "sunil": "सुनील", "vijay": "विजय", "sanjay": "संजय",
    "ajay": "अजय", "manoj": "मनोज", "vinod": "विनोद", "ashok": "अशोक", "gopal": "गोपाल",
    "kumar": "कुमार", "sharma": "शर्मा", "verma": "वर्मा", "yadav": "यादव", "singh": "सिंह",
    "gupta": "गुप्ता", "mishra": "मिश्रा", "pandey": "पांडे", "paswan": "पासवान", "rai": "राय",
    "devi": "देवी", "khan": "खान",
}
_FIRST = ["raju", "ramesh", "suresh", "mahesh", "dinesh", "mukesh", "anil", "sunil", "vijay", "sanjay",
          "ajay", "manoj", "vinod", "ashok", "gopal", "irfan", "salman", "rashid", "imran", "shahid"]
_LAST = ["kumar", "sharma", "verma", "yadav", "singh", "gupta", "mishra", "pandey", "paswan", "rai", "khan", "ansari"]
_NICK = ["Sonu", "Bablu", "Guddu", "Pappu", "Munna", "Chintu", "Golu", "Bunty", "Tinku", "Monu"]
_VILLAGES = ["Rampur", "Sultanpur", "Shahpur", "Krishnapur", "Hariharpur", "Madhopur", "Dhanpur", "Bhagwanpur"]
_DISTRICTS = ["Gopalganj", "Siwan", "Motihari", "Raxaul", "Araria", "Kishanganj"]
_POLICE_STATIONS = ["Thawe", "Baikunthpur", "Kuchaikote", "Bhorey", "Sidhwalia", "Mirganj"]
_TRANSIT = ["Anand Vihar ISBT", "Kashmere Gate ISBT", "Gorakhpur Bus Stand"]
_SAFEHOUSE_AREAS = ["Sector 14", "Sector 22", "Sector 31", "Sector 45"]
_ORG_STEMS = ["Sai", "Shree", "Maa Durga", "Balaji", "Om", "Star", "Golden", "Royal"]
_ORG_TAILS = ["Placement Services Pvt Ltd", "Manpower Consultants", "Job Agency", "Travels Pvt Ltd"]
_STATE_CODES = ["DL", "UP", "HR", "BR"]
_UPI_BANKS = ["okaxis", "ybl", "paytm", "oksbi"]


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------

@dataclass
class GoldMention:
    doc_id: str
    text: str
    entity_type: str        # PERSON / PHONE / VEHICLE / LOCATION / ORGANIZATION / ACCOUNT
    cluster_id: str         # same real-world entity => same cluster id
    start: int
    end: int


@dataclass
class ScenarioBundle:
    documents: list = field(default_factory=list)      # SyntheticDocument
    gold_mentions: list = field(default_factory=list)  # GoldMention (text documents only)
    clusters: dict = field(default_factory=dict)       # cluster_id -> {"type","name","role"}
    plan: dict = field(default_factory=dict)           # planted structure for analytics checks


@dataclass
class _Person:
    cid: str
    first: str
    last: str
    role: str
    nick: Optional[str] = None
    mohammad: bool = False
    phones: list = field(default_factory=list)
    vehicle: Optional[str] = None
    account: Optional[str] = None

    @property
    def full(self) -> str:
        first = "Mohd. " + self.first.title() if self.mohammad else self.first.title()
        return f"{first} {self.last.title()}"


class _TextBuilder:
    """Builds a document while recording the exact character span of every
    entity written into it."""

    def __init__(self, doc_id: str):
        self.doc_id, self.text, self.gold = doc_id, "", []

    def t(self, s: str) -> "_TextBuilder":
        self.text += s
        return self

    def e(self, surface: str, etype: str, cluster: str) -> "_TextBuilder":
        start = len(self.text)
        self.text += surface
        self.gold.append(GoldMention(self.doc_id, surface, etype, cluster, start, len(self.text)))
        return self


# --------------------------------------------------------------------------
# Generator
# --------------------------------------------------------------------------

def _phone(rng: random.Random) -> str:
    return rng.choice("6789") + "".join(rng.choices("0123456789", k=9))


def _phone_fmt(number: str, rng: random.Random) -> str:
    style = rng.choice(["plain", "dash334", "dash55", "plus91"])
    if style == "dash334":
        return f"{number[:3]}-{number[3:6]}-{number[6:]}"
    if style == "dash55":
        return f"{number[:5]}-{number[5:]}"
    if style == "plus91":
        return f"+91 {number}"
    return number


def _plate(rng: random.Random) -> str:
    return (rng.choice(_STATE_CODES) + f"{rng.randint(1, 20)}"
            + "".join(rng.choices("ABCDEFGHJKLMNPRSTUVWXYZ", k=rng.choice([1, 2, 3])))
            + f"{rng.randint(1000, 9999)}")


def _typo(word: str, rng: random.Random) -> str:
    if len(word) < 5:
        return word
    i = rng.randint(1, len(word) - 2)
    kind = rng.choice(["drop", "swap"])
    if kind == "drop":
        return word[:i] + word[i + 1:]
    return word[:i] + word[i + 1] + word[i] + word[i + 2:]


def _person_surface(p: _Person, rng: random.Random, allow_hindi: bool = False) -> str:
    """One realistic way an FIR/surveillance note might write this person."""
    options = ["full", "full", "full", "honorific", "initial", "typo"]
    if p.mohammad:
        options += ["md"]
    if p.nick:
        options += ["nick", "nick"]
    if allow_hindi and p.first in _HINDI_NAMES and p.last in _HINDI_NAMES:
        options += ["hindi", "hindi"]
    kind = rng.choice(options)
    first = p.first.title()
    if kind == "honorific":
        return f"{rng.choice(['Shri', 'Mr.'])} {p.full}"
    if kind == "initial":
        return f"{first} {p.last[0].upper()}."
    if kind == "typo":
        return f"{_typo(first, rng)} {p.last.title()}"
    if kind == "md":
        return f"Md. {first} {p.last.title()}"
    if kind == "nick":
        return p.nick
    if kind == "hindi":
        return f"{_HINDI_NAMES[p.first]} {_HINDI_NAMES[p.last]}"
    return p.full


def generate_trafficking_ring(seed: Optional[int] = 26189, num_firs: int = 8,
                              noise: float = 0.5) -> ScenarioBundle:
    """Generate the ring. `seed` makes it reproducible; `noise` in [0,1]
    is the probability that a document is written in a degraded style
    (ALL-CAPS, Hindi, typos)."""
    rng = random.Random(seed)
    bundle = ScenarioBundle()
    uid = lambda: uuid.UUID(int=rng.getrandbits(128)).hex[:8]  # noqa: E731 - reproducible ids

    used_names: set = set()
    used_nicks: set = set()

    def new_person(role: str, **kw) -> _Person:
        # Distinct people get distinct names and nicknames. Two different
        # people with the same name are unresolvable from text alone (the
        # pipeline flags such merges for review -- see tests/
        # test_resolution_multisignal.py); a benchmark of *resolution*
        # must not contain cases with no right answer.
        cid = f"P-{role}-{uid()}"
        for _ in range(200):
            first, last = rng.choice(_FIRST), rng.choice(_LAST)
            if first not in {n[0] for n in used_names}:   # unique first names: 20 available, <=15 people
                break
        used_names.add((first, last))
        if kw.get("nick") in used_nicks:
            kw["nick"] = next((n for n in _NICK if n not in used_nicks), None)
        if kw.get("nick"):
            used_nicks.add(kw["nick"])
        p = _Person(cid=cid, first=first, last=last, role=role, **kw)
        bundle.clusters[cid] = {"type": "PERSON", "name": p.full, "role": role}
        return p

    handler = new_person("handler", nick=rng.choice(_NICK), mohammad=rng.random() < 0.5)
    handler.phones = [_phone(rng) for _ in range(3)]          # multiple SIMs
    handler.vehicle = _plate(rng)
    handler.account = "".join(rng.choices("0123456789", k=12))
    recruiters = [new_person("recruiter", nick=rng.choice(_NICK) if i % 2 == 0 else None) for i in range(4)]
    for r in recruiters:
        r.phones = [_phone(rng) for _ in range(rng.choice([1, 2]))]
        r.account = "".join(rng.choices("0123456789", k=rng.choice([11, 12, 14])))
    transporter = new_person("transporter")
    transporter.phones = [_phone(rng)]
    transporter.vehicle = _plate(rng)
    caretaker = new_person("safehouse_keeper")
    caretaker.phones = [_phone(rng)]
    complainants = [new_person("complainant") for _ in range(num_firs)]

    org_name = f"{rng.choice(_ORG_STEMS)} {rng.choice(_ORG_TAILS)}"
    org_cid = f"O-front-{uid()}"
    bundle.clusters[org_cid] = {"type": "ORGANIZATION", "name": org_name, "role": "front_org"}
    safehouse_area = rng.choice(_SAFEHOUSE_AREAS)
    safehouse_cid = f"L-safehouse-{uid()}"
    bundle.clusters[safehouse_cid] = {"type": "LOCATION", "name": f"{safehouse_area} Gurgaon", "role": "safehouse"}
    transit = rng.choice(_TRANSIT)
    transit_cid = f"L-transit-{uid()}"
    bundle.clusters[transit_cid] = {"type": "LOCATION", "name": transit, "role": "transit_hub"}

    def place(name: str) -> str:
        """Cluster id for a named place (registered once)."""
        cid = f"L-{name}"
        bundle.clusters.setdefault(cid, {"type": "LOCATION", "name": name, "role": "place"})
        return cid

    cluster_of_phone: dict = {}
    for person in [handler, transporter, caretaker, *recruiters]:
        for ph in person.phones:
            cid = f"PH-{ph}"
            cluster_of_phone[ph] = cid
            bundle.clusters[cid] = {"type": "PHONE", "name": ph, "role": f"phone_of_{person.role}"}
    for person in [handler, transporter]:
        cid = f"V-{person.vehicle}"
        bundle.clusters[cid] = {"type": "VEHICLE", "name": person.vehicle, "role": f"vehicle_of_{person.role}"}

    def phone_span(b: _TextBuilder, number: str) -> None:
        b.e(_phone_fmt(number, rng), "PHONE", cluster_of_phone[number])

    def person_span(b: _TextBuilder, p: _Person, allow_hindi=False, forced: Optional[str] = None) -> None:
        b.e(forced or _person_surface(p, rng, allow_hindi), "PERSON", p.cid)

    def alias_pair(b: _TextBuilder, p: _Person) -> None:
        """"Full Name alias Nick" -- an explicit same-person declaration."""
        b.e(p.full, "PERSON", p.cid).t(rng.choice([" alias ", " @ ", " urf "])).e(p.nick, "PERSON", p.cid)

    base = datetime(2026, 3, 1)
    documents: list = []

    # ---------------- FIRs from complainant families ----------------
    for i, comp in enumerate(complainants):
        doc_id = f"fir-{i + 1:03d}"
        b = _TextBuilder(doc_id)
        village, district, ps = rng.choice(_VILLAGES), rng.choice(_DISTRICTS), rng.choice(_POLICE_STATIONS)
        recruiter = recruiters[i % len(recruiters)]
        date = (base + timedelta(days=rng.randint(0, 150))).strftime("%d/%m/%Y")
        shouting = rng.random() < noise * 0.5
        hindi = rng.random() < noise * 0.5 and recruiter.first in _HINDI_NAMES and recruiter.last in _HINDI_NAMES \
            and comp.first in _HINDI_NAMES and comp.last in _HINDI_NAMES

        if hindi:
            b.t("दिनांक ").t(date).t(" को शिकायतकर्ता ")
            b.e(f"{_HINDI_NAMES[comp.first]} {_HINDI_NAMES[comp.last]}", "PERSON", comp.cid)
            b.t(" ने बताया कि उनकी बेटी को ")
            b.e(f"{_HINDI_NAMES[recruiter.first]} {_HINDI_NAMES[recruiter.last]}", "PERSON", recruiter.cid)
            b.t(" ने नौकरी का लालच देकर ले गया। मोबाइल नंबर ")
            phone_span(b, recruiter.phones[0])
            b.t(" पर संपर्क हुआ।")
            documents.append((doc_id, b, False))
            continue

        b.t(f"On {date}, complainant ").e(f"Shri {comp.full}" if rng.random() < 0.4 else comp.full, "PERSON", comp.cid)
        b.t(" r/o village ").e(village, "LOCATION", place(village)).t(", PS ").e(ps, "LOCATION", place(ps))
        b.t(", district ").e(district, "LOCATION", place(district)).t(", stated that her daughter was taken away by ")
        if recruiter.nick and rng.random() < 0.6:
            alias_pair(b, recruiter)
        else:
            person_span(b, recruiter)
        b.t(" on the pretext of a job in ").e("Delhi", "LOCATION", place("Delhi")).t(". The accused was last contacted on mobile ")
        phone_span(b, rng.choice(recruiter.phones))
        b.t(". ")
        if rng.random() < 0.7:
            b.t("The accused was seen with ")
            if handler.nick and rng.random() < 0.5:
                alias_pair(b, handler)
            else:
                person_span(b, handler)
            b.t(" near ").e(transit if transit != "Anand Vihar ISBT" else "Anand Vihar ISBT", "LOCATION", transit_cid).t(". ")
        if rng.random() < 0.5:
            b.t("A white tempo bearing registration ").e(_plate_variant(transporter.vehicle, rng), "VEHICLE",
                                                         f"V-{transporter.vehicle}").t(" was noted.")
        documents.append((doc_id, b, shouting))

    # ---------------- transport interception FIR ----------------
    b = _TextBuilder("fir-tr01")
    b.t("A tempo bearing registration ").e(_plate_variant(transporter.vehicle, rng), "VEHICLE", f"V-{transporter.vehicle}")
    b.t(" driven by ").e(_person_surface(transporter, rng), "PERSON", transporter.cid)
    b.t(" was intercepted at ").e(transit, "LOCATION", transit_cid).t(". The driver stated that ")
    alias_pair(b, handler) if handler.nick else person_span(b, handler)
    b.t(" had arranged the trip and could be reached on ")
    phone_span(b, handler.phones[0])
    b.t(" and ")
    phone_span(b, handler.phones[1])
    b.t(". The passengers were to be taken to ").e(f"{safehouse_area} Gurgaon", "LOCATION", safehouse_cid).t(".")
    documents.append(("fir-tr01", b, False))

    # ---------------- surveillance report ----------------
    b = _TextBuilder("surv-001")
    b.t("Surveillance report: ")
    person_span(b, handler)
    b.t(" was observed meeting ")
    person_span(b, transporter)
    b.t(" at ").e(transit, "LOCATION", transit_cid).t(". Both then proceeded in vehicle ")
    b.e(handler.vehicle, "VEHICLE", f"V-{handler.vehicle}").t(" to ").e(f"{safehouse_area} Gurgaon", "LOCATION", safehouse_cid)
    b.t(", where ").e(org_name, "ORGANIZATION", org_cid).t(" operates. ")
    person_span(b, caretaker)
    b.t(" opened the gate; his phone ")
    phone_span(b, caretaker.phones[0])
    b.t(" was later seen in contact with ")
    phone_span(b, handler.phones[2])
    b.t(".")
    documents.append(("surv-001", b, False))

    # ---------------- intelligence note ----------------
    b = _TextBuilder("intel-001")
    b.t("Source information indicates that ")
    alias_pair(b, handler) if handler.nick else person_span(b, handler)
    b.t(" runs recruitment through ").e(org_name, "ORGANIZATION", org_cid)
    b.t(" and uses numbers ")
    phone_span(b, handler.phones[0])
    b.t(" and ")
    phone_span(b, handler.phones[2])
    b.t(". Recruiters ")
    person_span(b, recruiters[0])
    b.t(" and ")
    person_span(b, recruiters[1])
    b.t(" are paid commission through UPI.")
    documents.append(("intel-001", b, False))

    # ---------------- assemble text documents ----------------
    for doc_id, b, shouting in documents:
        text = b.text.upper() if shouting else b.text
        # .upper() preserves length for ASCII; Devanagari is untouched by construction
        bundle.documents.append(SyntheticDocument(doc_id=doc_id, doc_type=DocumentType.FIR, text=text))
        for g in b.gold:
            g.text = text[g.start:g.end]
        bundle.gold_mentions.extend(b.gold)

    # ---------------- CDRs: hub-and-spoke bursts ----------------
    transport_days = [base + timedelta(days=d) for d in (20, 55, 90, 125)]
    for k, r in enumerate(recruiters):
        calls = []
        for day in transport_days[: 2 + (k % 3)]:
            start = day.replace(hour=rng.randint(9, 20), minute=0)
            for j in range(rng.randint(5, 8)):     # burst: several calls within minutes
                calls.append({"caller": r.phones[0], "callee": handler.phones[k % len(handler.phones)],
                              "timestamp": (start + timedelta(minutes=j * 2)).isoformat(),
                              "duration_seconds": rng.randint(15, 240)})
        bundle.documents.append(SyntheticDocument(
            doc_id=f"cdr-{k + 1:03d}", doc_type=DocumentType.CDR,
            structured={"calls": calls, "is_burst_pattern": True}))
    bundle.documents.append(SyntheticDocument(
        doc_id="cdr-101", doc_type=DocumentType.CDR,
        structured={"calls": [
            {"caller": handler.phones[0], "callee": transporter.phones[0],
             "timestamp": (day.replace(hour=6)).isoformat(), "duration_seconds": rng.randint(30, 200)}
            for day in transport_days]}))
    bundle.documents.append(SyntheticDocument(
        doc_id="cdr-102", doc_type=DocumentType.CDR,
        structured={"calls": [
            {"caller": transporter.phones[0], "callee": caretaker.phones[0],
             "timestamp": (day.replace(hour=22)).isoformat(), "duration_seconds": rng.randint(20, 120)}
            for day in transport_days]}))

    # ---------------- financial: commissions just under Rs 50,000 ----------------
    for k, r in enumerate(recruiters):
        txs = [{"amount": rng.randint(42_500, 49_900), "currency": "INR",
                "timestamp": (day + timedelta(days=1)).isoformat()} for day in transport_days[: 2 + (k % 3)]]
        bundle.documents.append(SyntheticDocument(
            doc_id=f"fin-{k + 1:03d}", doc_type=DocumentType.FINANCIAL_RECORD,
            structured={"sender_account": handler.account, "receiver_account": r.account,
                        "transactions": txs, "is_structuring_pattern": True}))
        for acct in (handler.account, r.account):
            bundle.clusters.setdefault(f"AC-{acct}", {"type": "ACCOUNT", "name": acct, "role": "bank_account"})

    bundle.plan = {
        "handler_cluster": handler.cid,
        "handler_names": [handler.full, handler.nick],
        "handler_phones": handler.phones,
        "recruiter_clusters": [r.cid for r in recruiters],
        "transporter_cluster": transporter.cid,
        "front_org": org_name,
        "safehouse": f"{safehouse_area} Gurgaon",
        "expected_alert_types": ["hub_and_spoke", "financial_structuring", "communication_burst"],
    }
    return bundle


def _plate_variant(plate: str, rng: random.Random) -> str:
    """Same registration written with/without separators."""
    m = re.fullmatch(r"([A-Z]{2})(\d{1,2})([A-Z]{1,3})(\d{4})", plate)
    if not m or rng.random() < 0.5:
        return plate
    return " ".join(m.groups()) if rng.random() < 0.5 else "-".join(m.groups())


if __name__ == "__main__":
    ring = generate_trafficking_ring()
    print(f"{len(ring.documents)} documents, {len(ring.gold_mentions)} gold mentions, "
          f"{len(ring.clusters)} real-world entities")
    print("planted:", ring.plan)
    for d in ring.documents[:3]:
        print(f"\n--- {d.doc_id} ---\n{d.text or d.structured}")
