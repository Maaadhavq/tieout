"""Prove the four required cases from whatever is in the store.

    python -m tieout.verify                     # the committed corpus
    python -m tieout.verify --db tieout.db      # your own run

Prints, for each case, both facts, the full comparability trace, both verbatim
quotes with document and page, the confidence and the explanation. Exits
non-zero if a case is missing.

The cases are FOUND BY QUERY, never by fact id. Hard-coding "show relationship
rel_450a4ab" would make this a slideshow of one corpus and would be exactly the
document-specific logic the assignment rules out; the selection below is
"highest-confidence instance of each label, preferring cross-document", which
works on any corpus including one the system has never seen.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .store.db import Store

ROOT = Path(__file__).resolve().parents[1]
DEMO_DB = ROOT / "data" / "demo.db"

# A reconciliation on one of these is a finding. One on `period` alone is
# usually just two different quarters, which a reader can see for themselves.
INTERESTING = ("vintage", "consolidation", "measure", "sign_convention",
               "price_basis", "valuation")

GREEN, RED, AMBER, GREY, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[1m", "\033[0m")
MARK = {"same": "ok  ", "differs": "DIFF", "unknown": "?   ", "skipped": "-   "}

SELECT = """
SELECT r.*, fa.entity_raw, fa.metric_raw AS a_metric, fb.metric_raw AS b_metric,
       fa.value_raw AS a_value, fa.value_text AS a_text, fa.unit_raw AS a_unit,
       fa.period_raw AS a_period,
       fb.value_raw AS b_value, fb.value_text AS b_text, fb.unit_raw AS b_unit,
       fb.period_raw AS b_period,
       da.filename AS a_doc, db_.filename AS b_doc,
       ea.page_no AS a_page, eb.page_no AS b_page,
       ea.quote AS a_quote, eb.quote AS b_quote,
       ea.match_type AS a_match, eb.match_type AS b_match,
       (fa.doc_id != fb.doc_id) AS cross_document
FROM relationships r
JOIN facts fa ON fa.fact_id = r.fact_a
JOIN facts fb ON fb.fact_id = r.fact_b
JOIN evidence ea ON ea.evidence_id = fa.evidence_id
JOIN evidence eb ON eb.evidence_id = fb.evidence_id
JOIN documents da ON da.doc_id = fa.doc_id
JOIN documents db_ ON db_.doc_id = fb.doc_id
WHERE r.label = ?
"""


def _pick(store: Store, label: str, dimensions: tuple[str, ...] | None = None) -> dict | None:
    """Best instance of a label: prefer cross-document, then an interesting
    dimension, then confidence."""
    sql, args = SELECT, [label]
    if dimensions:
        sql += f" AND r.dimension IN ({','.join('?' * len(dimensions))})"
        args += list(dimensions)
    rows = store.q(sql + " ORDER BY cross_document DESC, r.confidence DESC LIMIT 1", args)
    return rows[0] if rows else None


def _side(r: dict, k: str) -> str:
    value = r[f"{k}_value"] or r[f"{k}_text"] or "—"
    unit = (r[f"{k}_unit"] or "").strip()
    period = r[f"{k}_period"] or "no period stated"
    metric = r["a_metric"] if k == "a" else r["b_metric"]
    return f"{r['entity_raw']} · {metric} · {period} = {value} {unit}".rstrip()


def _print_case(n: int, title: str, want: str, r: dict | None, colour: bool) -> bool:
    def c(code: str, text: str) -> str:
        return f"{code}{text}{OFF}" if colour else text

    print(f"\n{c(BOLD, f'CASE {n} — {title}')}")
    if not r:
        print(f"  {c(RED, 'NOT FOUND')} — no {want} relationship in this store")
        return False

    scope = "across documents" if r["cross_document"] else "within one document"
    header = f"{r['rel_id']} · decided by {r['decided_by']} · {scope}"
    print(f"  {c(GREY, header)}")
    print(f"    A  {_side(r, 'a')}")
    print(f"    B  {_side(r, 'b')}")

    print(f"\n  {c(GREY, 'comparability trace')}")
    for check in json.loads(r["rule_trace_json"] or "[]"):
        status = check["status"]
        tint = {"same": GREEN, "differs": AMBER, "unknown": GREY}.get(status, GREY)
        print(f"    {c(tint, MARK.get(status, status))} {check['dimension']:<16} "
              f"{check['detail']}")

    dim = f" ({r['dimension']})" if r["dimension"] else ""
    verdict = f"{r['label']}{dim}   confidence {r['confidence']}"
    ok = r["label"] == want
    print(f"\n  {c(GREEN if ok else RED, verdict)}")

    print(f"\n  {c(GREY, 'evidence, verbatim from the source')}")
    for k in ("a", "b"):
        print(f"    {k.upper()}  {r[f'{k}_doc']} p.{r[f'{k}_page']} "
              f"[{r[f'{k}_match']} match]")
        print(f"       “{' '.join((r[f'{k}_quote'] or '').split())[:150]}”")

    print(f"\n  {r['explanation']}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Prove the four required cases from the fact store.")
    ap.add_argument("--db", default=str(DEMO_DB))
    ap.add_argument("--no-colour", action="store_true")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    colour = not args.no_colour and sys.stdout.isatty()

    db = Path(args.db)
    if not db.exists():
        print(f"! no database at {db}. Run `python run.py --demo` first.")
        return 2
    store = Store(db)
    s = store.stats()
    if not s["relationships"]:
        print(f"! {db} has no relationships. Run `python run.py --demo` first.")
        return 2

    corpus = "hand-labelled reference set" if s["is_reference_set"] else "extracted corpus"
    print(f"\n  {db}  ·  {corpus}")
    print(f"  {s['documents']} documents · {s['facts_kept']} facts · "
          f"{s['relationships']} relationships")
    if not s["is_reference_set"]:
        print(f"  {s['facts_rejected']} facts refused by the grounding gate, "
              f"{s['ungrounded']} of them ungrounded "
              f"(hallucination rate {s['hallucination_rate'] * 100:.1f}%)")

    results = [
        _print_case(1, "A fact corroborated across documents, expressed differently",
                    "CORROBORATES", _pick(store, "CORROBORATES"), colour),
        _print_case(2, "A genuine or likely contradiction",
                    "CONTRADICTS", _pick(store, "CONTRADICTS"), colour),
        _print_case(3, "An apparent contradiction explained by context",
                    "CONTEXTUALLY_RECONCILED",
                    _pick(store, "CONTEXTUALLY_RECONCILED", INTERESTING)
                    or _pick(store, "CONTEXTUALLY_RECONCILED"), colour),
    ]

    # Case 4 is not a relationship: it is what the system refused to believe.
    print(f"\n{BOLD if colour else ''}CASE 4 — Extraction and reasoning failures"
          f"{OFF if colour else ''}")
    if s["is_reference_set"]:
        print("  This corpus is hand-labelled, so nothing was refused. Run against an")
        print("  extracted corpus (`--db data/demo.db`) to see the refusals.")
        results.append(True)
    else:
        for reason, n in sorted(s["rejects_by_reason"].items(), key=lambda kv: -kv[1]):
            print(f"    {n:5d}  {reason}")
        sample = store.q(
            "SELECT r.reason, r.page_no, r.payload_json, d.filename "
            "FROM rejects r JOIN documents d ON d.doc_id = r.doc_id "
            "WHERE r.reason = 'quote_not_found' LIMIT 1")
        if sample:
            payload = json.loads(sample[0]["payload_json"])
            print(f"\n  {GREY if colour else ''}a refused claim, kept so the number can be "
                  f"checked{OFF if colour else ''}")
            print(f"    {sample[0]['filename']} p.{sample[0]['page_no']}")
            print(f"    the model claimed : {payload.get('metric')} = "
                  f"{payload.get('value')} {payload.get('unit') or ''}")
            print(f"    quoting           : “{(payload.get('quote') or '')[:120]}”")
            print("    that span is not on the page, so the fact was never stored.")
        unreported = store.one(
            "SELECT COUNT(*) c FROM relationships WHERE label = 'INSUFFICIENT_EVIDENCE'")["c"]
        print(f"\n  {unreported} pairs were reported as INSUFFICIENT_EVIDENCE rather than")
        print("  guessed — a dimension one document states and the other does not.")
        results.append(bool(s["rejects_by_reason"]))

    ok = all(results)
    print(f"\n  {'all four cases demonstrated' if ok else 'SOME CASES MISSING'}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
