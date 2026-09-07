#!/usr/bin/env python
"""Tie-Out entrypoint.

    python run.py --demo     replay the committed extraction cache, no API key
    python run.py            ingest data/starter-datasets and serve
    python run.py --serve    serve whatever is already in the database
    python run.py --paths a.pdf b.pdf
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Windows consoles default to cp1252 and these documents are full of ₹ and ≡.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from tieout.api import app as api_app  # noqa: E402
from tieout.ingest.extract import Extractor  # noqa: E402
from tieout.ingest.pipeline import ingest_document  # noqa: E402
from tieout.reason import build as build_mod  # noqa: E402
from tieout.store.db import Store  # noqa: E402

STARTER = ROOT / "data" / "starter-datasets"
DEMO_DB = ROOT / "data" / "demo.db"
GOLD_DB = ROOT / "data" / "gold.db"


def find_pdfs(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        path = Path(p)
        out.extend(sorted(path.rglob("*.pdf")) if path.is_dir() else [path])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Tie-Out — a fact knowledge layer with an audit trail")
    ap.add_argument("--demo", action="store_true",
                    help="replay the committed extraction cache; no API key needed")
    ap.add_argument("--gold", action="store_true",
                    help="load the hand-labelled reference set; no API key needed")
    ap.add_argument("--serve", action="store_true", help="skip ingest, just serve")
    ap.add_argument("--paths", nargs="*", default=None, help="PDFs or directories to ingest")
    ap.add_argument("--db", default=None)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-serve", action="store_true", help="ingest then exit")
    ap.add_argument("--rebuild", action="store_true", help="recompute all relationships")
    args = ap.parse_args()

    db = Path(args.db) if args.db else (
        GOLD_DB if args.gold else DEMO_DB if args.demo else ROOT / "tieout.db")
    offline = args.demo or args.gold or not os.environ.get("GEMINI_API_KEY")

    if offline and not (args.demo or args.gold or args.serve):
        print("! No GEMINI_API_KEY found. Pages not already cached will be skipped.")
        print("  Use `python run.py --demo` to replay the committed cache.\n")

    store = Store(db)
    print(f"  database   {db}")
    print(f"  mode       {'cached replay (no API calls)' if offline else 'live extraction'}\n")

    if args.gold:
        from tieout.goldstore import build_gold_store
        if not store.q("SELECT 1 FROM facts LIMIT 1"):
            rep = build_gold_store(store)
            print(f"  loaded the hand-labelled reference set: {rep['facts']} facts "
                  f"from {rep['documents']} documents")
            for fid, why in rep["failed"]:
                print(f"      ! {fid}: {why}")
        rep = build_mod.rebuild_all(store, offline=True)
        print(f"  {rep.pairs_considered} pairs considered, {rep.written} relationships written")
        for label, n in sorted(rep.by_label.items(), key=lambda kv: -kv[1]):
            print(f"      {label:26s} {n}")
    elif not args.serve:
        pdfs = find_pdfs(args.paths) if args.paths else find_pdfs([str(STARTER)])
        if not pdfs:
            print(f"! No PDFs found. Looked in {STARTER}")
        extractor = Extractor(store, offline=offline)
        t0 = time.time()
        for pdf in pdfs:
            started = time.time()
            rep = ingest_document(store, pdf, extractor, workers=args.workers)
            if rep.already_ingested:
                print(f"  = {pdf.name[:52]:54s} already ingested ({rep.kept} facts)")
                continue
            print(f"  + {pdf.name[:52]:54s} {rep.pages_scanned:3d}/{rep.pages_total:3d} pages · "
                  f"{rep.kept:4d} kept · {rep.rejected:3d} rejected · {time.time() - started:5.1f}s")
            for reason, n in sorted(rep.reject_reasons.items(), key=lambda kv: -kv[1]):
                print(f"      rejected: {reason} ×{n}")
            if rep.errors:
                print(f"      {len(rep.errors)} page errors, first: {rep.errors[0][:90]}")

        print(f"\n  ingest took {time.time() - t0:.1f}s "
              f"({extractor.calls} model calls, {extractor.cache_hits} cache hits)")

        print("\n  relating facts…")
        rep = build_mod.rebuild_all(store, offline=offline) if args.rebuild \
            else build_mod.build(store, offline=offline)
        print(f"  {rep.pairs_considered} pairs considered "
              f"(naive would be {rep.pairs_if_naive}), {rep.written} relationships written, "
              f"{rep.adjudications} adjudication calls")
        for label, n in sorted(rep.by_label.items(), key=lambda kv: -kv[1]):
            print(f"      {label:26s} {n}")

    s = store.stats()
    print(f"\n  {s['facts_kept']} facts · {s['facts_rejected']} rejected by the grounding gate "
          f"· hallucination rate {s['hallucination_rate'] * 100:.1f}%")

    if args.no_serve:
        return 0

    api_app.configure(db, offline=offline)
    import uvicorn
    print(f"\n  → http://{args.host}:{args.port}\n")
    uvicorn.run(api_app.app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
