"""HTTP API and the evidence viewer.

The API is the contract; the page in web/ is a client of it. Every screen in
the UI is reachable as JSON, so the system can be inspected with curl alone.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse, Response, StreamingResponse
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


MAX_UPLOAD_MB = int(os.environ.get("TIEOUT_MAX_UPLOAD_MB", "50"))
_CHUNK = 1 << 20


def _safe_dest(filename: str | None) -> Path:
    """Resolve an upload destination that cannot escape data/uploads/.

    The filename is attacker-controlled. `UPLOADS / filename` with
    "../../../evil.pdf" writes outside the repository entirely, so take the
    basename and then check the resolved path really is inside UPLOADS -- the
    second check catches anything the first misses on a platform whose path
    rules differ from this one's.
    """
    name = Path(filename or "").name.strip()
    if not name or name in {".", ".."} or not name.lower().endswith(".pdf"):
        raise HTTPException(400, "expected a .pdf file")
    dest = (UPLOADS / name).resolve()
    if not str(dest).startswith(str(UPLOADS.resolve())):
        raise HTTPException(400, "invalid filename")
    return dest


async def _write_capped(src, dest: Path) -> None:
    """Stream to disk, refusing anything over the cap and anything that is not
    actually a PDF. Checking the magic bytes matters because ingest of a
    non-PDF raises deep inside PyMuPDF, which would surface as a 500."""
    limit = MAX_UPLOAD_MB * 1024 * 1024
    written, head = 0, b""
    try:
        with dest.open("wb") as fh:
            while chunk := await src.read(_CHUNK):
                if not head:
                    head = chunk[:5]
                    if not head.startswith(b"%PDF"):
                        raise HTTPException(
                            400, "that file is not a PDF (no %PDF header)")
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        413, f"file is larger than the {MAX_UPLOAD_MB} MB limit")
                fh.write(chunk)
        if not written:
            raise HTTPException(400, "the uploaded file is empty")
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise


@app.post("/api/documents")
async def upload(file: UploadFile = File(...)):
    """Accepts a new PDF, extracts it, and relates it to existing knowledge
    without recomputing anything already known."""
    dest = _safe_dest(file.filename)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    await _write_capped(file, dest)

    st = store()
    extractor = Extractor(st, offline=_offline)
    try:
        report = ingest_document(st, dest, extractor)
        rel = build_mod.build(st, incremental_doc_id=report.doc_id, offline=_offline)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - a bad document is the user's answer, not a crash
        dest.unlink(missing_ok=True)
        raise HTTPException(
            400, f"could not read that PDF: {type(exc).__name__}: {exc}") from exc
    return {
        "doc_id": report.doc_id,
        "filename": report.filename,
        "already_ingested": report.already_ingested,
        # Without a key the extractor replays the cache and nothing else. A new
        # document has no cached pages, so it yields nothing -- which the client
        # has to be able to explain rather than report as "0 facts kept".
        "offline": _offline,
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


# ------------------------------------------------------------------ export
# Every row carries its own provenance -- document, page, the verbatim quote and
# how that quote matched. A figure that lands in a spreadsheet without the
# sentence it came from is exactly the thing this system exists to avoid, so the
# export refuses to be a bag of numbers.
FACT_COLUMNS = [
    ("entity", "entity_raw"), ("metric", "metric_raw"), ("kind", "fact_kind"),
    ("value_as_written", "value_raw"), ("value_text", "value_text"),
    ("value_canonical", "value_num"), ("unit", "unit_canon"),
    ("unit_as_written", "unit_raw"), ("magnitude", "magnitude_raw"),
    ("period_as_written", "period_raw"), ("period_start", "period_start"),
    ("period_end", "period_end"), ("period_grain", "period_grain"),
]
BASIS_COLUMNS = ["consolidation", "vintage", "price_basis", "valuation",
                 "measure", "geography", "adjustment"]
PROVENANCE_COLUMNS = [
    ("source_document", "filename"), ("page", "page_no"), ("quote", "quote"),
    ("quote_match", "match_type"), ("confidence", "confidence"),
    ("grounding_confidence", "grounding_conf"),
    ("frame_completeness", "frame_completeness"), ("fact_id", "fact_id"),
]


NEWLINE = chr(10)


def _csv_response(header: list[str], rows, stem: str) -> StreamingResponse:
    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator=NEWLINE)
        writer.writerow(header)
        yield buf.getvalue()
        for row in rows:
            buf.seek(0), buf.truncate(0)
            writer.writerow(row)
            yield buf.getvalue()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return StreamingResponse(
        generate(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{stem}-{stamp}.csv"'})


@app.get("/api/export.csv")
def export_facts(entity: str | None = None, metric: str | None = None,
                 doc: str | None = None, kind: str | None = None,
                 min_confidence: float = 0.0, q: str | None = None,
                 limit: int = Query(50000, le=200000)):
    """The fact ledger as a spreadsheet, one row per fact, provenance included.

    Takes the same filters as /api/facts, so whatever is on screen is what
    exports."""
    facts_rows = facts(entity=entity, metric=metric, doc=doc, kind=kind,
                       min_confidence=min_confidence, q=q, limit=limit)
    header = ([h for h, _ in FACT_COLUMNS]
              + [f"basis_{b}" for b in BASIS_COLUMNS]
              + [h for h, _ in PROVENANCE_COLUMNS])

    def rows():
        for f in facts_rows:
            basis = json.loads(f.get("basis_json") or "{}")
            yield ([f.get(k) for _, k in FACT_COLUMNS]
                   + [basis.get(b) for b in BASIS_COLUMNS]
                   + [f.get(k) for _, k in PROVENANCE_COLUMNS])

    return _csv_response(header, rows(), "tieout-facts")


@app.get("/api/export/relationships.csv")
def export_relationships(label: str | None = None, entity: str | None = None,
                         dimension: str | None = None,
                         min_confidence: float = 0.0,
                         limit: int = Query(50000, le=200000)):
    """Every relationship with both sides, both sources, and the explanation."""
    rels = relationships(label=label, entity=entity, dimension=dimension,
                         min_confidence=min_confidence, limit=limit)
    header = ["label", "dimension", "confidence", "decided_by", "entity", "metric",
              "a_value", "a_unit", "a_period", "a_document", "a_page", "a_fact_id",
              "b_metric", "b_value", "b_unit", "b_period", "b_document", "b_page",
              "b_fact_id", "value_delta", "explanation", "rule_trace"]

    def rows():
        for r in rels:
            trace = " | ".join(
                f"{c['dimension']}={c['status']}"
                for c in json.loads(r.get("rule_trace_json") or "[]"))
            yield [r["label"], r["dimension"], r["confidence"], r["decided_by"],
                   r["entity_raw"], r["metric_raw"],
                   r["a_value"] or r["a_text"], r["a_unit"], r["a_period"],
                   r["a_doc"], r["a_page"], r["fact_a"],
                   r["b_metric"], r["b_value"] or r["b_text"], r["b_unit"],
                   r["b_period"], r["b_doc"], r["b_page"], r["fact_b"],
                   r["value_delta"], r["explanation"], trace]

    return _csv_response(header, rows(), "tieout-relationships")


@app.get("/api/export/refused.csv")
def export_refused(limit: int = Query(50000, le=200000)):
    """What the gate would not accept, with the model's own output intact.

    Exported for the same reason it is stored: a hallucination rate nobody can
    audit is just a number. This is the working."""
    rows_in = store().q(
        "SELECT r.*, d.filename FROM rejects r JOIN documents d ON d.doc_id = r.doc_id "
        "ORDER BY r.reason, r.created_at LIMIT ?", (limit,))
    header = ["reason", "source_document", "page", "claimed_entity", "claimed_metric",
              "claimed_value", "claimed_unit", "claimed_period",
              "quote_the_model_offered", "reject_id"]

    def rows():
        for r in rows_in:
            try:
                p = json.loads(r["payload_json"])
            except (ValueError, TypeError):
                p = {}
            yield [r["reason"], r["filename"], r["page_no"], p.get("entity"),
                   p.get("metric"), p.get("value") or p.get("value_text"),
                   p.get("unit"), p.get("period"), p.get("quote"), r["reject_id"]]

    return _csv_response(header, rows(), "tieout-refused")


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


def asset_stamp() -> str:
    """A token that changes whenever the stylesheet or the script does.

    Browsers cache /static/app.js hard enough that an edit to the frontend is
    invisible on reload -- you end up debugging code that is no longer on disk.
    Stamping the URLs with the assets' own mtimes makes a changed file a
    changed URL, so the cache is correct instead of merely being fought.

    Derived from BOTH files rather than the newer of the two: taking the max
    meant editing whichever file happened to be older left the stamp untouched,
    which is the same stale asset with extra steps.
    """
    parts = []
    for name in ("app.js", "styles.css"):
        try:
            st = (WEB / name).stat()
            parts.append(f"{name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{name}:absent")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


@app.get("/")
def index():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("/static/app.js", f"/static/app.js?v={asset_stamp()}")
                            .replace("/static/styles.css", f"/static/styles.css?v={asset_stamp()}"))


if WEB.exists():
    app.mount("/static", StaticFiles(directory=WEB), name="static")
