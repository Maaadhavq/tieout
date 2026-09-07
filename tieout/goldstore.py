"""Build a store from the hand-labelled gold set.

This is NOT extraction output and is never presented as such. It is the
reference corpus: eleven facts located by hand in the starter PDFs before any
extractor existed, pushed through the REAL grounding, normalization,
comparison and explanation code.

Two uses:
  * it proves the pipeline end to end without an API key, so the four required
    cases are reproducible by anyone who clones the repo;
  * it is the fixture the evaluation harness scores against.

`python run.py --gold` loads it. The UI labels the corpus accordingly.
"""
from __future__ import annotations

import json
from pathlib import Path

from .goldset import load_raw
from .ingest.ground import locate
from .ingest.pdf import read_pages, sha256_file
from .normalize import basis as basis_mod
from .normalize.entities import entity_key
from .normalize.metrics import metric_key
from .normalize.periods import parse_period
from .normalize.units import parse_quantity
from .store.db import new_id, now

DATA = Path(__file__).resolve().parents[1] / "data" / "starter-datasets"


def build_gold_store(store, data_dir: Path | None = None) -> dict:
    data_dir = Path(data_dir or DATA)
    gold = load_raw()
    docs: dict[str, str] = {}
    pages_cache: dict[str, list] = {}
    kept, failed = 0, []

    for g in gold["facts"]:
        rel = g["doc"]
        path = data_dir / rel
        if not path.exists():
            failed.append((g["id"], "source PDF missing"))
            continue

        if rel not in docs:
            pages = read_pages(path)
            pages_cache[rel] = pages
            doc_id = new_id("doc")
            docs[rel] = doc_id
            store.insert("documents", {
                "doc_id": doc_id, "filename": path.name, "path": str(path.resolve()),
                "sha256": sha256_file(path), "page_count": len(pages),
                "publisher": None, "doc_type": None, "as_of_date": None,
                "ingested_at": now(), "pages_scanned": len(pages), "pages_skipped": 0,
            })

        doc_id = docs[rel]
        page = pages_cache[rel][g["page"] - 1]

        # The same gate every extracted fact passes through.
        gr = locate(page, g["quote"])
        if not gr.ok:
            failed.append((g["id"], f"quote did not ground on p{g['page']}"))
            continue

        qty = parse_quantity(
            str(g["value"]) if g.get("value") is not None else None,
            g.get("unit"), g.get("magnitude"))
        period = parse_period(g.get("period") or g.get("effective"))
        known, leftover = basis_mod.normalize_basis(g.get("basis"), context=g["quote"])
        for dim, val in basis_mod.infer_from_label(g["metric"]).items():
            known.setdefault(dim, val)
        if g.get("status"):
            leftover["status"] = g["status"]

        ev_id = new_id("ev")
        store.insert("evidence", {
            "evidence_id": ev_id, "doc_id": doc_id, "page_no": g["page"],
            "quote": gr.located_text or g["quote"],
            "char_start": gr.char_start, "char_end": gr.char_end,
            "bbox_json": json.dumps(gr.bboxes), "match_type": gr.match_type,
        })

        mkey = metric_key(g["metric"])
        frame = sum([
            1, 1,
            1 if period else 0,
            1 if qty and qty.unit != "unknown" else 0,
            1 if known else 0,
        ]) / 5
        store.insert("facts", {
            "fact_id": g["id"], "doc_id": doc_id, "evidence_id": ev_id,
            "fact_kind": g.get("kind", "measurement"),
            "entity_raw": g["entity"], "entity_key": entity_key(g["entity"]),
            "metric_raw": g["metric"], "metric_key": mkey,
            "value_raw": str(g["value"]) if g.get("value") is not None else None,
            "value_num": qty.value if qty else None,
            "value_text": g.get("value_text") or g.get("status"),
            "value_tol": qty.tolerance if qty else None,
            "unit_raw": g.get("unit"), "unit_canon": qty.unit if qty else None,
            "magnitude_raw": qty.magnitude if qty else g.get("magnitude"),
            "period_raw": g.get("period") or g.get("effective"),
            "period_start": period.start.isoformat() if period else None,
            "period_end": period.end.isoformat() if period else None,
            "period_grain": period.grain if period else "unknown",
            "basis_json": json.dumps(known), "qualifiers_json": json.dumps(leftover),
            "extraction_model": "hand-labelled reference set",
            "extraction_conf": 1.0, "grounding_conf": gr.confidence,
            "frame_completeness": round(frame, 3),
            "confidence": round(gr.confidence * (0.5 + 0.5 * frame)
                                * (period.confidence if period else 0.6), 3),
            "created_at": now(),
        })
        store.intern_metric(mkey, g["metric"])
        kept += 1

    # The one alias the rules cannot derive, recorded with its source so it is
    # never mistaken for a model decision.
    store.insert("metric_aliases", {
        "key_a": "revenue_from_operations", "key_b": "revenue_from_services",
        "same": 1,
        "rationale": ("The Q4 FY24 deck reports revenue from services as ₹8,142 Cr and the "
                      "annual report reports revenue from operations as ₹81,415.38 million "
                      "for the same period; the deck's own footnote confirms these differ "
                      "only by revenue from traded goods, which was immaterial in FY24."),
        "decided_by": "rule",
    })
    return {"documents": len(docs), "facts": kept, "failed": failed}
