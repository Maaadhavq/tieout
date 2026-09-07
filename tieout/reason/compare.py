"""The comparator.

    Comparability is a separate, prior question to agreement.

Two facts are compared along the five dimensions of their CLAIM FRAME --
entity, metric, unit, period, basis -- before their values are looked at at
all. What the frame check finds decides the label:

    frame identical, values agree            -> CORROBORATES
    frame identical, values differ           -> CONTRADICTS
    frame differs on exactly one dimension   -> CONTEXTUALLY_RECONCILED
    frame cannot be established              -> INSUFFICIENT_EVIDENCE

No model participates in this. Every verdict is a walk through the checks
below, and the walk itself is returned as `rule_trace` so a reader can see
what the system actually did rather than take its word for it.

A pair whose frame differs on TWO OR MORE dimensions is not reported at all:
those facts are simply about different things, and emitting them as
"reconciled" would bury the real findings in noise.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..normalize import basis as basis_mod
from ..normalize.entities import same_entity
from ..normalize.metrics import same_metric
from ..normalize.periods import Period, relation
from ..normalize.units import Quantity, agree, comparable_units

CORROBORATES = "CORROBORATES"
CONTRADICTS = "CONTRADICTS"
RECONCILED = "CONTEXTUALLY_RECONCILED"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
UNRELATED = "UNRELATED"
# Not one of the four reported relationships. A document presenting the same
# metric for FY19, FY20 and FY21 differs on period at every pair, and calling
# each of those a "contextual reconciliation" is true but useless -- on the
# starter corpus it was 215 of 253, drowning the findings that matter.
# A reconciliation is only a finding when a reader could have mistaken the pair
# for a contradiction, and a document's own time series is not that: the
# periods are right there in the table. Counted, not reported.
TIME_SERIES = "TIME_SERIES"


@dataclass
class Check:
    dimension: str
    status: str           # same | differs | unknown | skipped
    detail: str

    def as_dict(self) -> dict:
        return {"dimension": self.dimension, "status": self.status, "detail": self.detail}


@dataclass
class Verdict:
    label: str
    dimension: str | None
    confidence: float
    checks: list[Check] = field(default_factory=list)
    value_delta: float | None = None
    needs_adjudication: bool = False
    adjudication_question: str | None = None

    def trace(self) -> list[dict]:
        return [c.as_dict() for c in self.checks]

    def comparability(self) -> dict:
        return {c.dimension: c.status for c in self.checks}


def _period_of(f: dict) -> Period | None:
    from datetime import date
    if not f.get("period_start") or not f.get("period_end"):
        return None
    return Period(
        date.fromisoformat(f["period_start"]),
        date.fromisoformat(f["period_end"]),
        f.get("period_grain") or "unknown",
    )


def _quantity_of(f: dict) -> Quantity | None:
    if f.get("value_num") is None:
        return None
    return Quantity(
        value=f["value_num"],
        tolerance=f.get("value_tol") or 0.0,
        unit=f.get("unit_canon") or "unknown",
        magnitude=f.get("magnitude_raw"),
        raw=f.get("value_raw") or "",
    )


def compare(a: dict, b: dict, aliases: dict | None = None) -> Verdict:
    checks: list[Check] = []

    # ---- 1. entity ---------------------------------------------------
    ent_same, ent_reason = same_entity(a["entity_key"], b["entity_key"])
    checks.append(Check("entity", "same" if ent_same else "differs", ent_reason))
    if not ent_same:
        return Verdict(UNRELATED, "entity", 1.0, checks)

    # ---- 2. metric ---------------------------------------------------
    met_same, met_reason = same_metric(a["metric_key"], b["metric_key"], aliases)
    if met_same is None:
        checks.append(Check("metric", "unknown", met_reason))
        v = Verdict(INSUFFICIENT, "metric", 0.4, checks)
        v.needs_adjudication = True
        v.adjudication_question = (
            f'Do "{a["metric_raw"]}" and "{b["metric_raw"]}" refer to the same measured quantity?'
        )
        return v
    checks.append(Check("metric", "same" if met_same else "differs", met_reason))
    if not met_same:
        return Verdict(UNRELATED, "metric", 1.0, checks)

    # ---- 3. unit -----------------------------------------------------
    qa, qb = _quantity_of(a), _quantity_of(b)
    is_measurement = qa is not None and qb is not None
    if is_measurement:
        if not comparable_units(qa, qb):
            detail = f"{qa.unit} vs {qb.unit}"
            if qa.unit != qb.unit and "unknown" not in (qa.unit, qb.unit):
                checks.append(Check("unit", "differs", detail + " — not converted, no rate in evidence"))
                return Verdict(INSUFFICIENT, "unit", 0.5, checks)
            checks.append(Check("unit", "unknown", detail))
            return Verdict(INSUFFICIENT, "unit", 0.35, checks)
        checks.append(Check("unit", "same", qa.unit))
    else:
        checks.append(Check("unit", "skipped", "non-numeric claim"))

    # ---- 4. period ---------------------------------------------------
    pa, pb = _period_of(a), _period_of(b)
    if pa is None or pb is None:
        which = a["doc_id"] if pa is None else b["doc_id"]
        checks.append(Check("period", "unknown", f"no reporting period stated in {which}"))
        return Verdict(INSUFFICIENT, "period", 0.45, checks)

    rel = relation(pa, pb)
    if rel == "same":
        checks.append(Check("period", "same", f"{a['period_raw']} ≡ {b['period_raw']} → "
                                              f"{pa.start} … {pa.end}"))
    else:
        human = {
            "contains": f"{a['period_raw']} contains {b['period_raw']}",
            "contained_by": f"{b['period_raw']} contains {a['period_raw']}",
            "overlaps": f"{a['period_raw']} partially overlaps {b['period_raw']}",
            "disjoint": f"{a['period_raw']} and {b['period_raw']} do not overlap",
        }[rel]
        checks.append(Check("period", "differs", human))

    # ---- 5. basis ----------------------------------------------------
    ba = json.loads(a.get("basis_json") or "{}")
    bb = json.loads(b.get("basis_json") or "{}")
    differs, unknown = basis_mod.compare_basis(ba, bb)
    for dim in differs:
        checks.append(Check(dim, "differs", f"{ba.get(dim)} vs {bb.get(dim)}"))
    for dim in unknown:
        stated, missing = (ba.get(dim), "B") if ba.get(dim) else (bb.get(dim), "A")
        checks.append(Check(dim, "unknown",
                            f"one document states {basis_mod.HUMAN.get(dim, dim)} = {stated}; "
                            f"the other does not say"))
    for dim in basis_mod.DIMENSIONS:
        if ba.get(dim) and bb.get(dim) and ba[dim] == bb[dim]:
            checks.append(Check(dim, "same", ba[dim]))

    frame_diffs = [c.dimension for c in checks if c.status == "differs"]
    frame_unknown = [c.dimension for c in checks if c.status == "unknown"]

    # ---- verdict -----------------------------------------------------
    if len(frame_diffs) >= 2:
        return Verdict(UNRELATED, ", ".join(frame_diffs), 1.0, checks)

    if len(frame_diffs) == 1:
        dim = frame_diffs[0]
        # A document's own time series: same source, period the only difference,
        # and the two periods do not overlap. Nothing here could be mistaken for
        # a disagreement. A quarter sitting inside its own year still can be, so
        # containment stays a reconciliation.
        if (dim == "period" and rel == "disjoint"
                and a.get("doc_id") and a["doc_id"] == b.get("doc_id")):
            return Verdict(TIME_SERIES, "period", 1.0, checks)
        conf = 0.9 if not frame_unknown else 0.7
        conf *= min(a.get("confidence", 1.0) or 1.0, b.get("confidence", 1.0) or 1.0) ** 0.25
        v = Verdict(RECONCILED, dim, round(conf, 2), checks)
        if is_measurement:
            v.value_delta = round(abs(qa.value - qb.value), 6)
        return v

    # frame is identical on every dimension both documents stated
    if not is_measurement:
        same_text = (a.get("value_text") or "").strip().lower() == (b.get("value_text") or "").strip().lower()
        checks.append(Check("value", "same" if same_text else "differs",
                            f'"{a.get("value_text")}" vs "{b.get("value_text")}"'))
        label = CORROBORATES if same_text else CONTRADICTS
        conf = 0.75 if frame_unknown else 0.85
        return Verdict(label, None, round(conf, 2), checks)

    ok, delta = agree(qa, qb)
    tol = max(qa.tolerance, qb.tolerance)

    # Same magnitude, opposite sign. Filings write a loss as 2,491.86 in the
    # narrative and (2,491.86) in the statements; that is one figure under two
    # sign conventions, not two claims.
    if not ok and abs(qa.value + qb.value) <= tol and qa.value * qb.value < 0:
        checks.append(Check("sign_convention", "differs",
                            f"{qa.raw} vs {qb.raw} — equal magnitude, opposite sign"))
        return Verdict(RECONCILED, "sign_convention", 0.8, checks, value_delta=round(delta, 6))

    checks.append(Check(
        "value", "same" if ok else "differs",
        f"{qa.raw} vs {qb.raw} · Δ {delta:.6g} · tolerance ±{tol:.6g} "
        f"(precision of the rounder source)",
    ))

    if frame_unknown:
        # Values disagree but a dimension is unstated: we cannot tell a real
        # disagreement from an unlabelled one. Say so rather than pick.
        if not ok:
            v = Verdict(INSUFFICIENT, frame_unknown[0], 0.6, checks)
            v.value_delta = round(delta, 6)
            return v
        v = Verdict(CORROBORATES, None, 0.75, checks)
        v.value_delta = round(delta, 6)
        return v

    conf = 0.93 * (min(a.get("confidence", 1.0) or 1.0, b.get("confidence", 1.0) or 1.0) ** 0.25)

    # A document contradicting ITSELF on ONE PAGE is possible but rare. Far more
    # often both values sit in the same table under different row headers --
    # current vs non-current borrowings, employees vs workers -- and the
    # qualifier that separates them was lost, because reading order flattens a
    # table into a stream. Measured on the starter corpus, most same-page
    # contradictions were this. The finding is still reported, at much lower
    # confidence and saying why, rather than asserted or hidden.
    same_page = (a.get("doc_id") and a["doc_id"] == b.get("doc_id")
                 and a.get("page_no") is not None and a.get("page_no") == b.get("page_no"))
    if not ok and same_page:
        checks.append(Check(
            "provenance", "unknown",
            "both values are on the same page under the same label; a table row "
            "qualifier that was not captured most likely separates them"))
        v = Verdict(CONTRADICTS, "provenance", round(conf * 0.45, 2), checks)
        v.value_delta = round(delta, 6)
        return v

    v = Verdict(CORROBORATES if ok else CONTRADICTS, None, round(conf, 2), checks)
    v.value_delta = round(delta, 6)
    return v
