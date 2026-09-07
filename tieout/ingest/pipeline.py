"""Document ingest: PDF -> pages -> model -> grounding gate -> normalized facts.

The gate sits between the model and the store. Nothing the model says reaches
`facts` without a quote that was found in the PDF; everything it says that
fails is written to `rejects` and counted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..normalize import basis as basis_mod
from ..normalize.entities import entity_key, is_resolvable
from ..normalize.metrics import metric_key
from ..normalize.periods import parse_period
from ..normalize.units import parse_quantity
from ..store.db import new_id, now
from . import prefilter
from .extract import Extractor
from .ground import locate
from .pdf import read_pages, sha256_file

FRAME_DIMS = ("entity", "metric", "period", "unit", "basis")


@dataclass
class IngestReport:
    doc_id: str
    filename: str
    pages_total: int = 0
    pages_scanned: int = 0
    pages_skipped: int = 0
    emitted: int = 0
    kept: int = 0
    rejected: int = 0
    reject_reasons: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    already_ingested: bool = False

    @property
    def hallucination_rate(self) -> float:
        return round(self.rejected / self.emitted, 4) if self.emitted else 0.0


def _frame_completeness(f: dict) -> float:
    """Fraction of the claim frame the document actually stated.

    An inferred dimension is not a stated one; this is what lets the reasoner
    say 'the source did not specify a period' instead of guessing."""
    present = 0
    present += 1 if f.get("entity_key") else 0
    present += 1 if f.get("metric_key") else 0
    present += 1 if f.get("period_start") else 0
    present += 1 if f.get("unit_canon") and f["unit_canon"] != "unknown" else 0
    present += 1 if json.loads(f.get("basis_json") or "{}") else 0
    return round(present / len(FRAME_DIMS), 3)


def ingest_document(store, path: str | Path, extractor: Extractor,
                    workers: int = 6, progress=None, force: bool = False) -> IngestReport:
    path = Path(path)
    sha = sha256_file(path)

    existing = store.doc_by_hash(sha)
    if existing and not force:
        r = IngestReport(existing["doc_id"], path.name, already_ingested=True)
        r.pages_total = existing["page_count"]
        r.kept = store.one(
            "SELECT COUNT(*) c FROM facts WHERE doc_id = ?", (existing["doc_id"],)
        )["c"]
        return r

    pages = read_pages(path)
    doc_id = existing["doc_id"] if existing else new_id("doc")
    report = IngestReport(doc_id, path.name, pages_total=len(pages))

    scan, skipped = prefilter.select(pages)
    report.pages_scanned, report.pages_skipped = len(scan), len(skipped)

    store.insert("documents", {
        "doc_id": doc_id, "filename": path.name, "path": str(path.resolve()),
        "sha256": sha,
        "page_count": len(pages), "publisher": None, "doc_type": None,
        "as_of_date": None, "ingested_at": now(),
        "pages_scanned": len(scan), "pages_skipped": len(skipped),
    })

    by_no = {p.number: p for p in pages}
    results = extractor.extract_pages(scan, workers=workers, progress=progress)

    for res in results:
        if res.error:
            report.errors.append(f"p{res.page_no}: {res.error}")
            continue
        page = by_no[res.page_no]
        for raw in res.facts:
            report.emitted += 1
            reason = _store_one(store, doc_id, page, raw)
            if reason:
                report.rejected += 1
                report.reject_reasons[reason] = report.reject_reasons.get(reason, 0) + 1
            else:
                report.kept += 1

    return report


def _reject(store, doc_id, page_no, reason, payload):
    store.insert("rejects", {
        "reject_id": new_id("rej"), "doc_id": doc_id, "page_no": page_no,
        "reason": reason, "payload_json": json.dumps(payload, ensure_ascii=False),
        "created_at": now(),
    })
    return reason


def _store_one(store, doc_id: str, page, raw: dict) -> str | None:
    """Returns a rejection reason, or None if the fact was stored."""
    quote = (raw.get("quote") or "").strip()
    if not quote:
        return _reject(store, doc_id, page.number, "no_quote", raw)

    ent_raw = (raw.get("entity") or "").strip()
    met_raw = (raw.get("metric") or "").strip()
    if not ent_raw or not met_raw:
        return _reject(store, doc_id, page.number, "no_entity_or_metric", raw)

    # "our Company", "the Group" -- a first-person reference names nothing on
    # its own, and blocking on it would compare every filing's "company" facts
    # against every other's. See entities.GENERIC.
    ekey = entity_key(ent_raw)
    if not is_resolvable(ekey):
        return _reject(store, doc_id, page.number, "unresolvable_entity", raw)

    # ---- THE GATE ----------------------------------------------------
    g = locate(page, quote)
    if not g.ok:
        return _reject(store, doc_id, page.number, "quote_not_found", raw)

    kind = raw.get("fact_kind") or "measurement"
    qty = parse_quantity(raw.get("value"), raw.get("unit"), raw.get("magnitude"))
    value_text = (raw.get("value_text") or "").strip() or None

    if kind == "measurement" and qty is None:
        if not value_text:
            return _reject(store, doc_id, page.number, "unparseable_value", raw)
        kind = "state"

    period = parse_period(raw.get("period"))
    context = g.located_text or quote
    known_basis, leftover = basis_mod.normalize_basis(raw.get("basis"), context=context)
    # Dimensions stated inside the metric label itself ("real GDP growth") are
    # moved into the basis, because metric_key strips them out.
    for dim, val in basis_mod.infer_from_label(met_raw).items():
        known_basis.setdefault(dim, val)

    ev_id = new_id("ev")
    store.insert("evidence", {
        "evidence_id": ev_id, "doc_id": doc_id, "page_no": page.number,
        "quote": g.located_text or quote,
        "char_start": g.char_start, "char_end": g.char_end,
        "bbox_json": json.dumps(g.bboxes),
        "match_type": g.match_type,
    })

    mkey = metric_key(met_raw)
    fact = {
        "fact_id": new_id("f"), "doc_id": doc_id, "evidence_id": ev_id,
        "fact_kind": kind,
        "entity_raw": ent_raw, "entity_key": ekey,
        "metric_raw": met_raw, "metric_key": mkey,
        "value_raw": raw.get("value"),
        "value_num": qty.value if qty else None,
        "value_text": value_text,
        "value_tol": qty.tolerance if qty else None,
        "unit_raw": raw.get("unit"),
        "unit_canon": qty.unit if qty else None,
        "magnitude_raw": qty.magnitude if qty else raw.get("magnitude"),
        "period_raw": raw.get("period"),
        "period_start": period.start.isoformat() if period else None,
        "period_end": period.end.isoformat() if period else None,
        "period_grain": period.grain if period else "unknown",
        "basis_json": json.dumps(known_basis, ensure_ascii=False),
        "qualifiers_json": json.dumps(leftover, ensure_ascii=False),
        "extraction_model": None,
        "extraction_conf": 1.0,
        "grounding_conf": g.confidence,
        "frame_completeness": 0.0,
        "confidence": 0.0,
        "created_at": now(),
    }
    fact["frame_completeness"] = _frame_completeness(fact)
    period_conf = period.confidence if period else 0.6
    fact["confidence"] = round(
        fact["extraction_conf"] * fact["grounding_conf"]
        * (0.5 + 0.5 * fact["frame_completeness"]) * period_conf, 3
    )

    store.insert("facts", fact)
    store.intern_metric(mkey, met_raw)
    return None
