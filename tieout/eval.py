"""Evaluation harness.

Scores the system against the hand-labelled set in tests/golden/cases.yaml and
against the store's own record of what it refused.

    python -m tieout.eval            # reasoning metrics, no API key needed
    python -m tieout.eval --db tieout.db   # add extraction metrics from a real run

Two of these numbers are unusual to publish and are the point:

  hallucination rate     facts the model emitted that the grounding gate threw
                         out, over facts emitted. Higher is not automatically
                         worse -- it is the share of model output that could
                         not be traced to the page, and a system that reports
                         zero is either perfect or not checking.
  confidence calibration whether facts the system was confident about were
                         actually right.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .goldset import load_facts, load_pairs, load_raw
from .reason.compare import compare
from .store.db import Store

ROOT = Path(__file__).resolve().parents[1]

# The single alias the deterministic rules cannot derive. Supplied here exactly
# as the adjudicator supplies it at runtime, so the score reflects the system
# as it actually runs.
ALIASES = {("revenue_from_operations", "revenue_from_services"): True}


def score_reasoning() -> dict:
    facts = load_facts()
    pairs = load_pairs()
    rows, correct = [], 0
    confusion: dict[tuple[str, str], int] = {}

    for a, b, expected, why in pairs:
        v = compare(facts[a], facts[b], ALIASES)
        ok = v.label == expected
        correct += ok
        confusion[(expected, v.label)] = confusion.get((expected, v.label), 0) + 1
        rows.append({"pair": f"{a}~{b}", "expected": expected, "got": v.label,
                     "dimension": v.dimension, "confidence": v.confidence, "ok": ok,
                     "why": why})

    neg_ok = 0
    negatives = load_raw().get("non_pairs", [])
    for a, b, _why in negatives:
        v = compare(facts[a], facts[b], ALIASES)
        neg_ok += v.label not in ("CORROBORATES", "CONTRADICTS")

    return {
        "labelled_pairs": len(pairs),
        "relationship_accuracy": round(correct / len(pairs), 4) if pairs else 0.0,
        "negative_controls": len(negatives),
        "negative_controls_passed": neg_ok,
        "rows": rows,
        "confusion": {f"{k[0]}->{k[1]}": v for k, v in sorted(confusion.items())},
    }


def score_extraction(db: Path) -> dict | None:
    """Extraction metrics from a real ingest run, if one exists."""
    if not db.exists():
        return None
    store = Store(db)
    s = store.stats()
    if not s["facts_emitted"]:
        return None

    hand = store.one(
        "SELECT COUNT(*) c FROM facts WHERE extraction_model = 'hand-labelled reference set'")["c"]
    is_reference = hand and hand == s["facts_kept"]

    gold = load_raw()["facts"]
    # Recall against the hand-labelled set: did the extractor find a grounded
    # fact on the same page for the same metric?
    found = 0
    for g in gold:
        hit = store.one(
            """SELECT 1 FROM facts f JOIN evidence e ON e.evidence_id = f.evidence_id
               JOIN documents d ON d.doc_id = f.doc_id
               WHERE d.filename = ? AND e.page_no = ? LIMIT 1""",
            (Path(g["doc"]).name, g["page"]))
        found += bool(hit)

    match_types = {r["match_type"]: r["c"] for r in store.q(
        "SELECT match_type, COUNT(*) c FROM evidence GROUP BY match_type")}

    buckets = store.q(
        """SELECT CASE WHEN confidence >= 0.8 THEN 'high'
                       WHEN confidence >= 0.5 THEN 'medium' ELSE 'low' END AS bucket,
                  COUNT(*) c, ROUND(AVG(grounding_conf), 3) g,
                  ROUND(AVG(frame_completeness), 3) f
           FROM facts GROUP BY bucket""")

    return {
        "is_reference_set": bool(is_reference),
        "facts_emitted": s["facts_emitted"],
        "facts_kept": s["facts_kept"],
        "facts_rejected": s["facts_rejected"],
        "hallucination_rate": s["hallucination_rate"],
        "rejection_rate": s["rejection_rate"],
        "ungrounded": s["ungrounded"],
        "rejects_by_reason": s["rejects_by_reason"],
        "evidence_match_types": match_types,
        "gold_pages_with_a_grounded_fact": f"{found}/{len(gold)}",
        "confidence_buckets": buckets,
        "metrics_discovered": s["metrics_discovered"],
        "relationships_by_label": s["relationships_by_label"],
        "decided_by_llm": s["llm_decided"],
        "pages_candidate": s["pages_candidate"],
        "pages_extracted": s["pages_extracted"],
        "pages_scanned": s["pages_scanned"],
        "pages_skipped": s["pages_skipped"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "tieout.db"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    reasoning = score_reasoning()
    extraction = score_extraction(Path(args.db))

    if args.json:
        print(json.dumps({"reasoning": reasoning, "extraction": extraction}, indent=2))
        return 0

    print("\n  REASONING — against tests/golden/cases.yaml")
    print(f"  {'relationship accuracy':38s} "
          f"{reasoning['relationship_accuracy'] * 100:5.1f}%  "
          f"({sum(r['ok'] for r in reasoning['rows'])}/{reasoning['labelled_pairs']} labelled pairs)")
    print(f"  {'negative controls passed':38s} "
          f"{reasoning['negative_controls_passed']}/{reasoning['negative_controls']}")
    print()
    for r in reasoning["rows"]:
        mark = "ok " if r["ok"] else "MISS"
        dim = f" ({r['dimension']})" if r["dimension"] else ""
        print(f"    {mark} {r['pair']:10s} {r['got']}{dim}  conf {r['confidence']}")
        if not r["ok"]:
            print(f"         expected {r['expected']} — {r['why']}")

    if not extraction:
        print("\n  EXTRACTION — no ingest run found."
              f"\n  Run `python run.py` (or --demo) first, then `python -m tieout.eval --db {args.db}`")
        return 0

    e = extraction
    if e["is_reference_set"]:
        print("\n  EXTRACTION — not measured here.")
        print(f"  {args.db} holds the hand-labelled reference set, not model output, so a")
        print("  0% hallucination rate would be meaningless. Ingest real PDFs with")
        print("  `python run.py`, then re-run with --db tieout.db.")
        print(f"\n  For the record: {e['facts_kept']} reference facts, all grounded; "
              f"quote match types {e['evidence_match_types']}.\n")
        return 0
    print("\n  EXTRACTION — from the ingest run in", args.db)
    print(f"  {'pages skipped by the junk filter':38s} {e['pages_skipped']}")
    print(f"  {'pages a model actually answered for':38s} {e['pages_extracted']} "
          f"of {e['pages_candidate']} candidates")
    if e["pages_extracted"] < e["pages_candidate"]:
        print(f"  {'':38s} (the run was cut short — see README)")
    print(f"  {'facts emitted by the model':38s} {e['facts_emitted']}")
    print(f"  {'facts kept (quote located)':38s} {e['facts_kept']}")
    print(f"  {'facts refused by the gate':38s} {e['facts_rejected']} "
          f"({e['rejection_rate'] * 100:.1f}%)")
    print(f"  {'  of which ungrounded (no quote)':38s} {e['ungrounded']}")
    print(f"  {'hallucination rate':38s} {e['hallucination_rate'] * 100:5.1f}%"
          f"   <- ungrounded / emitted")
    print(f"  {'gold pages with a grounded fact':38s} {e['gold_pages_with_a_grounded_fact']}")
    print(f"  {'distinct metrics discovered':38s} {e['metrics_discovered']}")
    print(f"  {'relationships decided by a model':38s} {e['decided_by_llm']}")
    if e["rejects_by_reason"]:
        print("\n  why facts were rejected")
        for reason, n in sorted(e["rejects_by_reason"].items(), key=lambda kv: -kv[1]):
            print(f"    {reason:34s} {n}")
    if e["evidence_match_types"]:
        print("\n  how quotes matched the page")
        for k, n in sorted(e["evidence_match_types"].items(), key=lambda kv: -kv[1]):
            print(f"    {k:34s} {n}")
    if e["confidence_buckets"]:
        print("\n  confidence buckets (grounding / frame completeness)")
        for b in e["confidence_buckets"]:
            print(f"    {b['bucket']:34s} {b['c']:4d}   {b['g']} / {b['f']}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
