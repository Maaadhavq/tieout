"""HTTP API and the evidence viewer.

The API is the contract; the page in web/ is a client of it. Every screen in
the UI is reachable as JSON, so the system can be inspected with curl alone.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from ..ingest.extract import Extractor
from ..ingest.pdf import RENDER_DPI, render_page_png
from ..ingest.pipeline import ingest_document
from ..reason import build as build_mod
from ..store.db import Store

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
UPLOADS = ROOT / "data" / "uploads"

app = FastAPI(title="Tie-Out", docs_url="/api/docs", openapi_url="/api/openapi.json")

_store: Store | None = None
_offline = False


def configure(db_path: str | Path, offline: bool = False) -> Store:
    global _store, _offline
    _store = Store(db_path)
    _offline = offline
    return _store


def store() -> Store:
    if _store is None:
        configure(os.environ.get("TIEOUT_DB", ROOT / "tieout.db"))
    return _store


# ---------------------------------------------------------------- documents
@app.get("/api/health")
def health():
    return {"status": "ok", "offline": _offline}


@app.get("/api/stats")
def stats():
    s = store().stats()
    s["offline"] = _offline
    return s


@app.get("/api/documents")
def documents():
    return store().q(
        """SELECT d.*,
                  (SELECT COUNT(*) FROM facts f WHERE f.doc_id = d.doc_id) AS facts,
                  (SELECT COUNT(*) FROM rejects r WHERE r.doc_id = d.doc_id) AS rejected
           FROM documents d ORDER BY d.ingested_at"""
    )


@app.post("/api/documents")
async def upload(file: UploadFile = File(...)):
    """Accepts a new PDF, extracts it, and relates it to existing knowledge
    without recomputing anything already known."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "only PDF uploads are accepted")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS / file.filename
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    st = store()
    extractor = Extractor(st, offline=_offline)
    report = ingest_document(st, dest, extractor)
    rel = build_mod.build(st, incremental_doc_id=report.doc_id, offline=_offline)
    return {
        "doc_id": report.doc_id,
        "filename": report.filename,
        "already_ingested": report.already_ingested,
        "pages_total": report.pages_total,
        "pages_scanned": report.pages_scanned,
        "pages_skipped": report.pages_skipped,
        "facts_kept": report.kept,
        "facts_rejected": report.rejected,
        "rejection_rate": report.rejection_rate,
        "hallucination_rate": report.hallucination_rate,
        "reject_reasons": report.reject_reasons,
        "relationships_added": rel.written,
        "relationships_by_label": rel.by_label,
        "errors": report.errors[:5],
    }


# -------------------------------------------------------------------- facts
FACT_SELECT = """
SELECT f.*, e.page_no, e.quote, e.bbox_json, e.match_type, d.filename
FROM facts f
JOIN evidence e ON e.evidence_id = f.evidence_id
JOIN documents d ON d.doc_id = f.doc_id
"""


@app.get("/api/facts")
def facts(entity: str | None = None, metric: str | None = None,
          doc: str | None = None, kind: str | None = None,
          min_confidence: float = 0.0, q: str | None = None,
          limit: int = Query(500, le=5000), offset: int = 0):
    where, args = ["f.confidence >= ?"], [min_confidence]
    if entity:
        where.append("f.entity_key LIKE ?"); args.append(f"%{entity.lower()}%")
    if metric:
        where.append("f.metric_key LIKE ?"); args.append(f"%{metric.lower()}%")
    if doc:
        where.append("f.doc_id = ?"); args.append(doc)
    if kind:
        where.append("f.fact_kind = ?"); args.append(kind)
    if q:
        where.append("(f.entity_raw LIKE ? OR f.metric_raw LIKE ? OR e.quote LIKE ?)")
        args += [f"%{q}%"] * 3
    sql = f"{FACT_SELECT} WHERE {' AND '.join(where)} ORDER BY f.entity_key, f.metric_key, f.period_start LIMIT ? OFFSET ?"
    return store().q(sql, args + [limit, offset])


@app.get("/api/facts/{fact_id}")
def fact(fact_id: str):
    row = store().one(f"{FACT_SELECT} WHERE f.fact_id = ?", (fact_id,))
    if not row:
        raise HTTPException(404, "no such fact")
    row["relationships"] = store().q(
        "SELECT * FROM relationships WHERE fact_a = ? OR fact_b = ? ORDER BY confidence DESC",
        (fact_id, fact_id),
    )
    return row


# ------------------------------------------------------------ relationships
def _relationships(where: list[str], args: list, limit: int) -> list[dict]:
    sql = f"""
      SELECT r.*, fa.entity_raw, fa.metric_raw,
             fa.value_raw AS a_value, fa.value_text AS a_text, fa.unit_raw AS a_unit,
             fa.period_raw AS a_period, fa.basis_json AS a_basis, fa.fact_id AS a_id,
             fb.value_raw AS b_value, fb.value_text AS b_text, fb.unit_raw AS b_unit,
             fb.period_raw AS b_period, fb.basis_json AS b_basis, fb.fact_id AS b_id,
             fb.metric_raw AS b_metric,
             da.filename AS a_doc, db_.filename AS b_doc,
             ea.page_no AS a_page, eb.page_no AS b_page
      FROM relationships r
      JOIN facts fa ON fa.fact_id = r.fact_a
      JOIN facts fb ON fb.fact_id = r.fact_b
      JOIN evidence ea ON ea.evidence_id = fa.evidence_id
      JOIN evidence eb ON eb.evidence_id = fb.evidence_id
      JOIN documents da ON da.doc_id = fa.doc_id
      JOIN documents db_ ON db_.doc_id = fb.doc_id
      WHERE {' AND '.join(where)}
      ORDER BY r.confidence DESC LIMIT ?"""
    return store().q(sql, args + [limit])


@app.get("/api/relationships")
def relationships(label: str | None = None, entity: str | None = None,
                  dimension: str | None = None, min_confidence: float = 0.0,
                  limit: int = Query(500, le=5000)):
    where, args = ["r.confidence >= ?"], [min_confidence]
    if label:
        where.append("r.label = ?"); args.append(label)
    if dimension:
        where.append("r.dimension = ?"); args.append(dimension)
    if entity:
        where.append("fa.entity_key LIKE ?"); args.append(f"%{entity.lower()}%")
    return _relationships(where, args, limit)


@app.get("/api/relationships/{rel_id}")
def relationship(rel_id: str):
    rows = _relationships(["r.rel_id = ?"], [rel_id], 1)
    if not rows:
        raise HTTPException(404, "no such relationship")
    r = rows[0]
    r["rule_trace"] = json.loads(r["rule_trace_json"])
    r["fact_a_full"] = fact(r["fact_a"])
    r["fact_b_full"] = fact(r["fact_b"])
    return r


@app.get("/api/rejects")
def rejects(limit: int = Query(200, le=2000)):
    return store().q(
        "SELECT r.*, d.filename FROM rejects r JOIN documents d ON d.doc_id = r.doc_id "
        "ORDER BY r.created_at DESC LIMIT ?", (limit,))


@app.get("/api/metrics")
def metrics():
    return store().q("SELECT * FROM metrics ORDER BY n_facts DESC")


# ---------------------------------------------------------------- rendering
def _resolve_pdf(doc: dict) -> Path | None:
    """Find the source PDF from a stored path that may be relative to the repo,
    absolute on another machine, or only known by filename."""
    stored = doc.get("path") or ""
    for candidate in (ROOT / stored, Path(stored)):
        if stored and candidate.exists():
            return candidate
    matches = list((ROOT / "data").rglob(doc["filename"]))
    return matches[0] if matches else None


@app.get("/api/pages/{doc_id}/{page_no}.png")
def page_png(doc_id: str, page_no: int):
    doc = store().one("SELECT * FROM documents WHERE doc_id = ?", (doc_id,))
    if not doc:
        raise HTTPException(404, "no such document")
    src = _resolve_pdf(doc)
    if not src:
        raise HTTPException(404, f"source PDF not found for {doc['filename']}")
    png = render_page_png(src, page_no)
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400",
                             "X-Render-Scale": str(RENDER_DPI / 72.0)})


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


if WEB.exists():
    app.mount("/static", StaticFiles(directory=WEB), name="static")
