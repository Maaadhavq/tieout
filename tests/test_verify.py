"""The one-command case verifier.

`python -m tieout.verify` is what an evaluator runs to confirm the four required
cases without watching the video. If it silently stops finding a case, the
submission's central claim quietly becomes false -- so it is tested.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from tieout import verify
from tieout.store.db import Store

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo.db"

pytestmark = pytest.mark.skipif(not DEMO.exists(), reason="demo corpus not present")


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "tieout.verify", "--no-colour", *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_all_four_cases_are_demonstrated_and_it_exits_zero():
    r = run("--db", str(DEMO))
    assert r.returncode == 0, r.stdout[-2000:]
    for case in ("CASE 1", "CASE 2", "CASE 3", "CASE 4"):
        assert case in r.stdout
    assert "all four cases demonstrated" in r.stdout
    assert "NOT FOUND" not in r.stdout


def test_each_case_shows_a_trace_a_verdict_and_verbatim_evidence():
    out = run("--db", str(DEMO)).stdout
    assert out.count("comparability trace") >= 3
    assert out.count("evidence, verbatim from the source") >= 3
    for label in ("CORROBORATES", "CONTRADICTS", "CONTEXTUALLY_RECONCILED"):
        assert label in out
    assert "p." in out, "no page references in the evidence"


def test_it_reports_what_the_gate_refused():
    out = run("--db", str(DEMO)).stdout
    assert "quote_not_found" in out
    assert "hallucination rate" in out
    assert "never stored" in out, "no refused claim was shown"


def test_a_missing_database_exits_non_zero_without_a_traceback(tmp_path):
    r = run("--db", str(tmp_path / "absent.db"))
    assert r.returncode == 2
    assert "Traceback" not in r.stderr


def test_an_empty_store_exits_non_zero(tmp_path):
    db = tmp_path / "empty.db"
    Store(db)
    r = run("--db", str(db))
    assert r.returncode == 2
    assert "Traceback" not in r.stderr


def test_cases_are_selected_by_query_not_by_hard_coded_ids():
    """If the verifier named specific fact or relationship ids it would be a
    slideshow of one corpus, and the assignment rules that out."""
    src = Path(verify.__file__).read_text(encoding="utf-8")
    import re
    assert not re.search(r"['\"](rel|f|ev)_[0-9a-f]{8,}['\"]", src), \
        "a concrete row id is hard-coded in the verifier"
    for token in ("delhivery", "rbi", "imf", "economic-survey"):
        assert token not in src.lower(), f"{token!r} is named in the verifier"


def test_it_works_against_the_reference_corpus_too():
    gold = ROOT / "data" / "gold.db"
    if not gold.exists():
        pytest.skip("gold corpus not built")
    r = run("--db", str(gold))
    assert r.returncode == 0, r.stdout[-1500:]
    assert "hand-labelled" in r.stdout
