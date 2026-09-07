"""End-to-end: PDF -> extract -> ground -> normalize -> store -> compare.

The rest of the suite tests the pieces. This one runs the whole thing on a real
starter PDF, replaying the committed extraction cache so it needs no API key and
no network. If the wiring between stages breaks, every other test can still pass
and this one will not.
"""
import json
import shutil
from pathlib import Path

import pytest

from tieout.ingest.extract import Extractor
from tieout.ingest.pipeline import ingest_document
from tieout.reason import build as build_mod
from tieout.store.db import Store

ROOT = Path(__file__).resolve().parents[1]
DEMO_DB = ROOT / "data" / "demo.db"
PDF = ROOT / "data" / "starter-datasets" / "india-macroeconomy" / "02-rbi-annual-report-2024-25-excerpt.pdf"

pytestmark = pytest.mark.skipif(
    not (DEMO_DB.exists() and PDF.exists()),
    reason="committed demo corpus or starter dataset not present")


@pytest.fixture(scope="module")
def ingested(tmp_path_factory):
    """A fresh store seeded ONLY with the cached model output, then run through
    the real pipeline. Nothing here talks to a model."""
    db = tmp_path_factory.mktemp("e2e") / "t.db"
    shutil.copy(DEMO_DB, db)
    store = Store(db)
    for table in ("relationships", "rejects", "facts", "evidence", "documents",
                  "metrics", "metric_aliases"):
        store.run(f"DELETE FROM {table}")

    extractor = Extractor(store, offline=True)
    report = ingest_document(store, PDF, extractor)
    build = build_mod.build(store, offline=True)
    return store, report, build, extractor


def test_ingest_produced_facts_without_any_model_call(ingested):
    store, report, _, extractor = ingested
    assert extractor.calls == 0, "the cached run should make no API calls"
    assert extractor.cache_hits > 0, "nothing was replayed from the cache"
    assert report.kept > 50, f"only {report.kept} facts from a 100-page report"


def test_every_stored_fact_has_locatable_evidence(ingested):
    """The core promise. A fact without a quote, a page and a box is a claim
    with no source, and none may exist in the store."""
    store, _, _, _ = ingested
    orphans = store.q("""
        SELECT f.fact_id FROM facts f
        LEFT JOIN evidence e ON e.evidence_id = f.evidence_id
        WHERE e.evidence_id IS NULL OR e.quote IS NULL OR TRIM(e.quote) = ''
           OR e.page_no IS NULL OR e.bbox_json IS NULL OR e.bbox_json = '[]'""")
    assert not orphans, f"{len(orphans)} facts have no usable evidence"


def test_no_fact_survives_a_failed_grounding(ingested):
    store, _, _, _ = ingested
    assert not store.q("SELECT 1 FROM evidence WHERE match_type = 'failed'")


def test_refused_facts_are_recorded_rather_than_dropped(ingested):
    """The hallucination rate depends on rejects being kept, with the model's
    own output, so the number can be checked rather than taken on trust."""
    store, report, _, _ = ingested
    rows = store.q("SELECT reason, payload_json FROM rejects")
    assert len(rows) == report.rejected
    for r in rows:
        assert r["reason"]
        assert json.loads(r["payload_json"])


def test_normalization_ran_on_the_stored_facts(ingested):
    store, _, _, _ = ingested
    assert store.one("SELECT COUNT(*) c FROM facts WHERE period_start IS NOT NULL")["c"] > 10
    assert store.one("SELECT COUNT(*) c FROM facts WHERE value_num IS NOT NULL")["c"] > 10
    assert store.one("SELECT COUNT(*) c FROM facts WHERE basis_json != '{}'")["c"] > 0


def test_relationships_are_built_and_carry_a_readable_trace(ingested):
    _, _, build, _ = ingested
    assert build.written > 0, "no relationships were produced"
    assert build.pairs_considered < build.pairs_if_naive, "blocking bought nothing"


def test_every_relationship_explains_itself(ingested):
    store, _, _, _ = ingested
    for r in store.q("SELECT rel_id, label, explanation, rule_trace_json FROM relationships"):
        trace = json.loads(r["rule_trace_json"])
        assert trace, f"{r['rel_id']} has an empty rule trace"
        assert all(c.get("dimension") and c.get("detail") for c in trace)
        assert len(r["explanation"]) > 40, f"{r['rel_id']} explanation is too thin"


def test_labels_are_only_the_four_reportable_ones(ingested):
    store, _, _, _ = ingested
    seen = {r["label"] for r in store.q("SELECT DISTINCT label FROM relationships")}
    assert seen <= {"CORROBORATES", "CONTRADICTS", "CONTEXTUALLY_RECONCILED",
                    "INSUFFICIENT_EVIDENCE"}, f"unexpected label leaked out: {seen}"


def test_reingesting_the_same_document_is_a_no_op(ingested):
    """Content-hash dedup: the evaluator dragging the same PDF in twice must not
    double the corpus."""
    store, _, _, _ = ingested
    before = store.one("SELECT COUNT(*) c FROM facts")["c"]
    again = ingest_document(store, PDF, Extractor(store, offline=True))
    assert again.already_ingested
    assert store.one("SELECT COUNT(*) c FROM facts")["c"] == before
