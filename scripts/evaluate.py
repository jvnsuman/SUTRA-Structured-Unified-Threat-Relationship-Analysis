"""
scripts/evaluate.py

Measures extraction, entity-resolution and relation quality against
labelled data, so "how good is it?" has a number.

Two labelled sets:
  * labelled  -- data/eval/labelled_firs.json: 20 hand-written FIR-style
                 documents in realistic Indian police-report style.
  * generated -- data/trafficking_scenario.py: a synthetic trafficking
                 ring with ground truth for every mention (larger, more
                 regular than real text -> expect optimistic numbers).

Metrics
  Extraction   precision / recall / F1 per entity type and micro-average.
               A predicted entity is correct if it has the same type and
               overlaps the gold span with IoU >= 0.5 (strict); a lenient
               any-overlap score is reported too.
  Resolution   over mentions that extraction found correctly (so it
               isolates resolution quality): pairwise precision / recall
               / F1 of "same real-world entity" links, the number of
               false merges (a resolved entity holding >1 gold entity),
               and fragmentation of the planted people (resolved entities
               per real person; 1 is perfect).
  Relations    precision / recall / F1 against gold (a, b, type) triples
               (labelled set only).

Usage
    python -m scripts.evaluate                       # both sets, prints a report
    python -m scripts.evaluate --json out.json       # also write metrics as JSON
    python -m scripts.evaluate --dump-bundle b.json  # write the labelled sets in a
                                                     # neutral format, so the SAME data can
                                                     # be scored against another checkout
    python -m scripts.evaluate --load-bundle b.json  # score a neutral bundle (works with
                                                     # an older version of nlp/)
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections import defaultdict
from itertools import combinations
from pathlib import Path

warnings.filterwarnings("ignore")

_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_labelled() -> dict:
    data = json.loads((_ROOT / "data" / "eval" / "labelled_firs.json").read_text(encoding="utf-8"))
    texts = {d["doc_id"]: d["text"] for d in data["documents"]}
    cursor: dict = defaultdict(int)
    gold = []
    for g in data["gold"]:
        text = texts[g["doc_id"]]
        start = text.find(g["text"], cursor[g["doc_id"]])
        if start < 0:
            raise ValueError(f"gold mention {g['text']!r} not found in {g['doc_id']} after offset {cursor[g['doc_id']]}")
        end = start + len(g["text"])
        cursor[g["doc_id"]] = end
        gold.append({"doc_id": g["doc_id"], "text": g["text"], "type": g["type"],
                     "cluster": g["cluster"], "start": start, "end": end})
    return {"documents": [{"doc_id": k, "text": v} for k, v in texts.items()],
            "gold": gold, "relations": data.get("relations", [])}


def load_generated(seed: int = 26189) -> dict:
    from data.trafficking_scenario import generate_trafficking_ring
    ring = generate_trafficking_ring(seed=seed)
    docs = [{"doc_id": d.doc_id, "text": d.text} for d in ring.documents if d.text]
    gold = [{"doc_id": g.doc_id, "text": g.text, "type": g.entity_type, "cluster": g.cluster_id,
             "start": g.start, "end": g.end} for g in ring.gold_mentions]
    return {"documents": docs, "gold": gold, "relations": []}


# --------------------------------------------------------------------------
# Scoring helpers
# --------------------------------------------------------------------------

def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3), "tp": tp, "fp": fp, "fn": fn}


def _iou(a0, a1, b0, b1) -> float:
    inter = max(0, min(a1, b1) - max(a0, b0))
    union = (a1 - a0) + (b1 - b0) - inter
    return inter / union if union else 0.0


def evaluate_set(bundle: dict) -> dict:
    from nlp.extraction import extract_entities
    from nlp.resolution import resolve_entities

    gold_by_doc: dict = defaultdict(list)
    for g in bundle["gold"]:
        gold_by_doc[g["doc_id"]].append(g)

    per_type: dict = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "ltp": 0})
    all_predicted, pred_to_cluster = [], {}
    type_confusions, errors = 0, []
    entities_by_doc = {}

    for doc in bundle["documents"]:
        preds = extract_entities(doc["text"], doc["doc_id"])
        entities_by_doc[doc["doc_id"]] = preds
        all_predicted.extend(preds)
        golds = gold_by_doc.get(doc["doc_id"], [])
        used = set()
        for g in golds:
            best, best_iou = None, 0.0
            for i, p in enumerate(preds):
                if i in used or p.entity_type.value != g["type"]:
                    continue
                iou = _iou(g["start"], g["end"], p.start_char, p.end_char)
                if iou > best_iou:
                    best, best_iou = i, iou
            if best is not None and best_iou >= 0.5:
                used.add(best)
                per_type[g["type"]]["tp"] += 1
                pred_to_cluster[preds[best].id] = g["cluster"]
            else:
                per_type[g["type"]]["fn"] += 1
                clash = [p for p in preds if _iou(g["start"], g["end"], p.start_char, p.end_char) > 0]
                if clash and all(p.entity_type.value != g["type"] for p in clash):
                    type_confusions += 1
                errors.append(("MISS", doc["doc_id"], g["type"], g["text"],
                               [f"{p.entity_type.value}:{p.text}" for p in clash]))
            if any(_iou(g["start"], g["end"], p.start_char, p.end_char) > 0 and p.entity_type.value == g["type"]
                   for p in preds):
                per_type[g["type"]]["ltp"] += 1
        for i, p in enumerate(preds):
            if i not in used:
                per_type[p.entity_type.value]["fp"] += 1
                errors.append(("EXTRA", doc["doc_id"], p.entity_type.value, p.text, []))

    extraction = {}
    tot = {"tp": 0, "fp": 0, "fn": 0}
    for t, c in sorted(per_type.items()):
        extraction[t] = _prf(c["tp"], c["fp"], c["fn"])
        for k in tot:
            tot[k] += c[k]
    extraction["MICRO"] = _prf(**tot)
    ltp = sum(c["ltp"] for c in per_type.values())
    extraction["MICRO"]["lenient_recall"] = round(ltp / max(1, sum(c["tp"] + c["fn"] for c in per_type.values())), 3)
    extraction["type_confusions"] = type_confusions

    # ---- resolution (over correctly extracted mentions) -----------------
    resolved = resolve_entities(all_predicted)
    mention_to_res = {m: r.id for r in resolved for m in r.mention_ids}
    scored_ids = [m for m in pred_to_cluster if m in mention_to_res]
    pred_pairs, gold_pairs = set(), set()
    for a, b in combinations(scored_ids, 2):
        same_res = mention_to_res[a] == mention_to_res[b]
        same_gold = pred_to_cluster[a] == pred_to_cluster[b]
        if same_res:
            pred_pairs.add((a, b))
        if same_gold:
            gold_pairs.add((a, b))
    tp = len(pred_pairs & gold_pairs)
    resolution = _prf(tp, len(pred_pairs - gold_pairs), len(gold_pairs - pred_pairs))

    by_res: dict = defaultdict(set)
    for m in scored_ids:
        by_res[mention_to_res[m]].add(pred_to_cluster[m])
    review_ids = {r.id for r in resolved if getattr(r, "needs_review", False)}
    false_ids = [rid for rid, clusters in by_res.items() if len(clusters) > 1]
    resolution["false_merged_entities"] = len(false_ids)
    # The safety-critical number: wrong merges the system did NOT flag for review.
    resolution["false_merged_unflagged"] = sum(1 for rid in false_ids if rid not in review_ids)
    resolution["resolved_entities_needing_review"] = sum(1 for r in resolved if getattr(r, "needs_review", False))

    frag: dict = defaultdict(set)
    for m in scored_ids:
        frag[pred_to_cluster[m]].add(mention_to_res[m])
    multi = {c: len(v) for c, v in frag.items() if sum(1 for x in scored_ids if pred_to_cluster[x] == c) > 1}
    resolution["multi_mention_entities"] = len(multi)
    resolution["perfectly_resolved_entities"] = sum(1 for v in multi.values() if v == 1)
    resolution["avg_fragments_per_multi_mention_entity"] = round(sum(multi.values()) / len(multi), 2) if multi else 0.0

    result = {"extraction": extraction, "resolution": resolution,
              "documents": len(bundle["documents"]), "gold_mentions": len(bundle["gold"]),
              "predicted_mentions": len(all_predicted)}

    # ---- relations (only where gold triples exist) ----------------------
    # Scored at entity level: a relation is (doc, cluster_a, cluster_b, type)
    # with endpoints unordered, so "Bablu" and "Ramesh Sharma" are the same
    # endpoint. Predicted endpoints that did not match a gold mention have no
    # cluster and count as false positives.
    if bundle.get("relations"):
        try:
            from nlp.relation_rules import classify_relations_rules as rel_fn
        except ImportError:
            rel_fn = None
        if rel_fn:
            gold_rel = {(doc, frozenset((a, b)), t) for doc, a, b, t in bundle["relations"]}
            pred_rel = set()
            for doc in bundle["documents"]:
                ents = {e.id: e for e in entities_by_doc[doc["doc_id"]]}
                for r in rel_fn(list(ents.values()), doc["text"]):
                    ca, cb = pred_to_cluster.get(r.entity_a_id), pred_to_cluster.get(r.entity_b_id)
                    pred_rel.add((doc["doc_id"], frozenset((ca or f"?{r.entity_a_id}", cb or f"?{r.entity_b_id}")),
                                  r.relation_type.value))
            tp = len(pred_rel & gold_rel)
            result["relations"] = _prf(tp, len(pred_rel - gold_rel), len(gold_rel - pred_rel))
            result["_relation_errors"] = {"fp": sorted(map(str, pred_rel - gold_rel))[:15],
                                          "fn": sorted(map(str, gold_rel - pred_rel))[:15]}

    result["_errors"] = errors
    return result


def aggregate(results: list) -> dict:
    """Sum counts across several evaluate_set() results and recompute the
    ratios (micro-averaging), so a group of generated rings reads as one."""
    agg: dict = {"extraction": {}, "resolution": {}, "documents": 0, "gold_mentions": 0, "predicted_mentions": 0}
    for r in results:
        for k in ("documents", "gold_mentions", "predicted_mentions"):
            agg[k] += r[k]
        for t, m in r["extraction"].items():
            if isinstance(m, dict):
                c = agg["extraction"].setdefault(t, {"tp": 0, "fp": 0, "fn": 0, "lr": 0.0, "n": 0})
                for k in ("tp", "fp", "fn"):
                    c[k] += m[k]
                if "lenient_recall" in m:
                    c["lr"] += m["lenient_recall"] * (m["tp"] + m["fn"])
                    c["n"] += m["tp"] + m["fn"]
        for k in ("tp", "fp", "fn", "false_merged_entities", "false_merged_unflagged",
                  "resolved_entities_needing_review", "multi_mention_entities", "perfectly_resolved_entities"):
            agg["resolution"][k] = agg["resolution"].get(k, 0) + r["resolution"].get(k, 0)
    out_ex = {}
    for t, c in agg["extraction"].items():
        out_ex[t] = _prf(c["tp"], c["fp"], c["fn"])
        if c["n"]:
            out_ex[t]["lenient_recall"] = round(c["lr"] / c["n"], 3)
    out_ex["type_confusions"] = sum(r["extraction"].get("type_confusions", 0) for r in results)
    rs = agg["resolution"]
    res = _prf(rs["tp"], rs["fp"], rs["fn"])
    res.update({k: rs[k] for k in rs if k not in ("tp", "fp", "fn")})
    res["avg_fragments_per_multi_mention_entity"] = round(
        sum(r["resolution"]["avg_fragments_per_multi_mention_entity"] * r["resolution"]["multi_mention_entities"]
            for r in results) / max(1, rs["multi_mention_entities"]), 2)
    agg["extraction"], agg["resolution"] = out_ex, res
    return agg


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def format_report(name: str, res: dict, show_errors: int = 0) -> str:
    lines = [f"\n=== {name}: {res['documents']} docs, {res['gold_mentions']} gold mentions, "
             f"{res['predicted_mentions']} predicted ===", "", "Extraction (IoU>=0.5, same type)",
             f"  {'type':<14}{'P':>7}{'R':>7}{'F1':>7}{'tp':>5}{'fp':>5}{'fn':>5}"]
    for t, m in res["extraction"].items():
        if isinstance(m, dict):
            lines.append(f"  {t:<14}{m['precision']:>7.3f}{m['recall']:>7.3f}{m['f1']:>7.3f}{m['tp']:>5}{m['fp']:>5}{m['fn']:>5}")
    lines.append(f"  lenient (any-overlap) micro recall: {res['extraction']['MICRO'].get('lenient_recall', 'n/a')}   "
                 f"type confusions: {res['extraction']['type_confusions']}")
    r = res["resolution"]
    lines += ["", "Resolution (pairwise same-entity links over correctly extracted mentions)",
              f"  P={r['precision']:.3f}  R={r['recall']:.3f}  F1={r['f1']:.3f}   "
              f"(tp={r['tp']} fp={r['fp']} fn={r['fn']})",
              f"  false-merged entities: {r['false_merged_entities']} (silent, i.e. NOT flagged for review: {r.get('false_merged_unflagged', 'n/a')})   "
              f"entities with 2+ mentions: {r['multi_mention_entities']}, fully resolved: {r['perfectly_resolved_entities']}   "
              f"avg fragments/entity: {r['avg_fragments_per_multi_mention_entity']}   "
              f"flagged for review: {r['resolved_entities_needing_review']}"]
    if "relations" in res:
        m = res["relations"]
        lines += ["", f"Relations (rule-based)  P={m['precision']:.3f}  R={m['recall']:.3f}  F1={m['f1']:.3f}  "
                      f"(tp={m['tp']} fp={m['fp']} fn={m['fn']})"]
    if show_errors and res.get("_relation_errors"):
        lines += ["", "Relation false positives / false negatives:"]
        lines += [f"  FP {x}" for x in res["_relation_errors"]["fp"]] + [f"  FN {x}" for x in res["_relation_errors"]["fn"]]
    if show_errors:
        lines += ["", f"First {show_errors} extraction errors:"]
        for kind, doc, typ, text, clash in res["_errors"][:show_errors]:
            lines.append(f"  {kind:<5} {doc:<9}{typ:<13}{text!r} {clash or ''}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", choices=["labelled", "generated", "both"], default="both")
    ap.add_argument("--seed", type=int, default=26189)
    ap.add_argument("--seeds", type=int, default=0,
                    help="also evaluate this many additional generated rings (seeds 1..N), reported as one aggregate")
    ap.add_argument("--json", help="write metrics to this JSON file")
    ap.add_argument("--dump-bundle", help="write the labelled sets in a neutral JSON format and exit")
    ap.add_argument("--load-bundle", help="evaluate a neutral bundle instead of loading the sets")
    ap.add_argument("--errors", type=int, default=0, help="print the first N extraction errors per set")
    args = ap.parse_args()
    sys.path.insert(0, str(_ROOT))

    if args.load_bundle:
        sets = json.loads(Path(args.load_bundle).read_text(encoding="utf-8"))
    else:
        sets = {}
        if args.set in ("labelled", "both"):
            sets["labelled (hand-written)"] = load_labelled()
        if args.set in ("generated", "both"):
            sets["generated (trafficking ring, seed %d)" % args.seed] = load_generated(args.seed)
            if args.seeds:
                sets["generated x%d seeds (1..%d), aggregate" % (args.seeds, args.seeds)] = [
                    load_generated(sd) for sd in range(1, args.seeds + 1)]

    if args.dump_bundle:
        Path(args.dump_bundle).write_text(json.dumps(sets, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.dump_bundle}")
        return

    out = {}
    for name, bundle in sets.items():
        if isinstance(bundle, list):
            res = aggregate([evaluate_set(b) for b in bundle])
        else:
            res = evaluate_set(bundle)
        print(format_report(name, res, args.errors if not isinstance(bundle, list) else 0))
        out[name] = {k: v for k, v in res.items() if not k.startswith("_")}
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
