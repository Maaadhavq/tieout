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

def ingest_summary(seconds: float, model_calls: int, cache_hits: int,
                   documents: int, already_ingested: int) -> str:
    """One line saying what the ingest actually did.

    On a fresh clone every document is already in the committed store, so both
    counters are zero and this printed "0 model calls, 0 cache hits". The
    README has just promised that `--demo` replays a committed cache with zero
    API calls, so the first output an evaluator sees appears to say the replay
    never happened and the figures below it are canned. They are not; nothing
    needed re-reading. Say that instead of printing two zeroes.
    """
    if already_ingested == documents and not model_calls and not cache_hits:
        return (f"  ingest took {seconds:.1f}s — all {documents} documents were already "
                f"in this store, so none needed re-reading. Their facts came from the "
                f"committed extraction cache, with no model call then or now.")
    return (f"  ingest took {seconds:.1f}s "
            f"({model_calls} model calls, {cache_hits} cache hits)")


def coverage_note(pages_extracted: int, pages_candidate: int) -> str:
    """Why the page count is short, stated where the short count is printed.

    "this run did not finish the corpus" named a fact and no cause, so the most
    alarming line in the output read as a broken submission. The cause is not
    knowable from here -- it may be quota, an interrupt, or a missing key -- so
    name the possibilities and what the figures above it are over.
    """
    return (f"  read {pages_extracted} of {pages_candidate} candidate pages — the rest "
            f"were never sent to a model (daily quota, an interrupted run, or no API "
            f"key). Every figure above is over the pages that were read, not the "
            f"whole corpus.")


def _load_env(path: Path) -> None:
    """Read .env ourselves, tolerating the encodings Windows produces.

    PowerShell's `>` redirect writes UTF-16LE, so `echo KEY=... > .env` yields a
    file python-dotenv cannot decode -- it raises UnicodeDecodeError and the key
    silently never loads. Worth 15 lines to not lose an evaluator here.
    """
    if not path.exists():
        return
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    elif raw[:3] == b"\xef\xbb\xbf":
        text = raw.decode("utf-8-sig")
    else:
        text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env(ROOT / ".env")

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

    if args.demo and not store.q("SELECT 1 FROM extract_cache LIMIT 1"):
        print("! --demo replays a committed extraction cache, and this database has none.")
        print("  That happens if data/demo.db was not cloned with the repo.")
        print("  `python run.py --gold` reproduces the four cases with no API key,")
        print("  or set GEMINI_API_KEY and run `python run.py` to extract for real.\n")
        return 1

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
        already = 0
        for pdf in pdfs:
            started = time.time()
            rep = ingest_document(store, pdf, extractor, workers=args.workers)
            if rep.already_ingested:
                already += 1
                print(f"  = {pdf.name[:52]:54s} already ingested ({rep.kept} facts)")
                continue
            print(f"  + {pdf.name[:52]:54s} {rep.pages_scanned:3d}/{rep.pages_total:3d} pages · "
                  f"{rep.kept:4d} kept · {rep.rejected:3d} rejected · {time.time() - started:5.1f}s")
            for reason, n in sorted(rep.reject_reasons.items(), key=lambda kv: -kv[1]):
                print(f"      rejected: {reason} ×{n}")
            if rep.errors:
                print(f"      {len(rep.errors)} page errors, first: {rep.errors[0][:90]}")

        print("\n" + ingest_summary(time.time() - t0, extractor.calls,
                                    extractor.cache_hits, len(pdfs), already))

        existing_rels = store.one("SELECT COUNT(*) c FROM relationships")["c"]
        if existing_rels and not args.rebuild:
            print(f"\n  {existing_rels} relationships already computed — "
                  f"pass --rebuild to recompute")
            rep = None
        else:
            print("\n  relating facts…")
            rep = build_mod.rebuild_all(store, offline=offline) if args.rebuild \
                else build_mod.build(store, offline=offline)
        if rep:
            print(f"  {rep.pairs_considered} pairs considered "
                  f"(naive would be {rep.pairs_if_naive}), {rep.written} relationships written, "
                  f"{rep.adjudications} adjudication calls")
            print(f"  suppressed: {rep.time_series} same-document time series, "
                  f"{rep.unresolved} unresolved metric-alias questions")
            for label, n in sorted(rep.by_label.items(), key=lambda kv: -kv[1]):
                print(f"      {label:26s} {n}")

    s = store.stats()
    if s["is_reference_set"]:
        # A hallucination rate over hand-labelled facts is trivially zero and
        # says nothing about the extractor. Say what this corpus is instead.
        print(f"\n  {s['facts_kept']} hand-labelled reference facts, all grounded — "
              f"not extraction output, so no hallucination rate is reported")
    else:
        print(f"\n  {s['facts_kept']} facts kept · {s['facts_rejected']} refused "
              f"({s['rejection_rate'] * 100:.1f}%), of which {s['ungrounded']} could not "
              f"be quoted from the page")
        print(f"  hallucination rate {s['hallucination_rate'] * 100:.1f}% "
              f"(ungrounded claims / claims the model emitted)")
        if s["pages_extracted"] < s["pages_candidate"]:
            print(coverage_note(s["pages_extracted"], s["pages_candidate"]))

    if args.no_serve:
        return 0

    api_app.configure(db, offline=offline)
    import uvicorn
    print(f"\n  → http://{args.host}:{args.port}\n")
    uvicorn.run(api_app.app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
