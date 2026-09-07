"""Build relationships over the fact store.

Full pass and incremental pass share one code path; the only difference is
which candidate pairs are generated. Because blocks are keyed on stored
columns, adding a document compares its facts against the members of the
blocks they join and leaves every existing relationship untouched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..store.db import new_id, now
from . import blocking
from .adjudicate import Adjudicator
from .compare import UNRELATED, compare
from .explain import explain

SUPPRESSED = {UNRELATED}


@dataclass
class BuildReport:
    facts: int = 0
    pairs_considered: int = 0
    pairs_if_naive: int = 0
    written: int = 0
    suppressed: int = 0
    adjudications: int = 0
    unresolved: int = 0
    by_label: dict = field(default_factory=dict)


def _facts(store, where: str = "", args=()) -> list[dict]:
    return store.q(f"SELECT * FROM facts {where}", args)


def _evidence_index(store) -> dict[str, dict]:
    rows = store.q(
        "SELECT e.*, d.filename FROM evidence e JOIN documents d ON d.doc_id = e.doc_id"
    )
    return {r["evidence_id"]: r for r in rows}


def build(store, incremental_doc_id: str | None = None, offline: bool = False,
          budget: int | None = None) -> BuildReport:
    adj = Adjudicator(store, offline=offline, **({"budget": budget} if budget else {}))
    ev = _evidence_index(store)
    report = BuildReport()

    if incremental_doc_id:
        new = _facts(store, "WHERE doc_id = ?", (incremental_doc_id,))
        old = _facts(store, "WHERE doc_id != ?", (incremental_doc_id,))
        pairs = blocking.pairs_for_new_facts(new, old)
        report.facts = len(new)
    else:
        allf = _facts(store)
        pairs = blocking.candidate_pairs(allf)
        report.facts = len(allf)
        report.pairs_if_naive = len(allf) * (len(allf) - 1) // 2

    report.pairs_considered = len(pairs)

    for a, b in pairs:
        v = compare(a, b, adj.aliases)

        if v.needs_adjudication:
            answer = adj.resolve(a["metric_key"], a["metric_raw"],
                                 b["metric_key"], b["metric_raw"])
            if answer is None:
                # An unanswered alias question is a question, not a finding.
                # Writing it as INSUFFICIENT_EVIDENCE would bury the real
                # results under thousands of near-miss metric pairs, so it is
                # counted and reported in /api/stats instead.
                report.unresolved += 1
                continue
            report.adjudications += 1
            v = compare(a, b, adj.aliases)

        if v.label in SUPPRESSED:
            report.suppressed += 1
            continue

        decided_by = "llm" if (v.needs_adjudication and v.label not in SUPPRESSED) else "rule"
        rationale = adj.rationale_for(a["metric_key"], b["metric_key"])
        text = explain(v, a, b, ev.get(a["evidence_id"]), ev.get(b["evidence_id"]))
        if rationale:
            text += f" Metric labels were matched by adjudication: {rationale}"

        store.insert("relationships", {
            "rel_id": new_id("rel"),
            "fact_a": a["fact_id"], "fact_b": b["fact_id"],
            "label": v.label, "dimension": v.dimension,
            "comparability_json": json.dumps(v.comparability()),
            "rule_trace_json": json.dumps(v.trace()),
            "value_delta": v.value_delta,
            "confidence": v.confidence,
            "decided_by": decided_by,
            "explanation": text,
            "created_at": now(),
        })
        report.written += 1
        report.by_label[v.label] = report.by_label.get(v.label, 0) + 1

    return report


def rebuild_all(store, offline: bool = False, budget: int | None = None) -> BuildReport:
    store.run("DELETE FROM relationships")
    return build(store, offline=offline, budget=budget)
