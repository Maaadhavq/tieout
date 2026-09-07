"""Human-readable explanations, written from the rule trace.

Explanations are TEMPLATED, not generated. A model asked to describe a verdict
will sometimes describe a different, more plausible one; a template can only
say what the checks actually found. The trace is the source of truth and the
prose is a rendering of it, so the two can never drift apart.

Where a dimension is unknown rather than different, the explanation says which
document failed to state it. That is the difference between "these contradict"
and "I cannot tell whether these contradict", and it is the sentence an
evaluator is most likely to read closely.
"""
from __future__ import annotations

from ..normalize import basis as basis_mod
from .compare import (CONTRADICTS, CORROBORATES, INSUFFICIENT, RECONCILED,
                      UNRELATED, Verdict)

DIMENSION_PHRASE = {
    "period": "the reporting periods are different",
    "vintage": "they are different vintages of the same measurement",
    "consolidation": "one is consolidated and the other is not",
    "price_basis": "they are stated on different price bases",
    "measure": "they measure different aggregates",
    "geography": "they cover different areas",
    "adjustment": "one is adjusted and the other is not",
    "unit": "they are stated in different units",
    "metric": "it is unclear whether they measure the same thing",
}

RECONCILE_HINT = {
    "period": "A figure for part of a year is not in conflict with the figure for the whole year.",
    "vintage": "Institutional publishers restate the same period as data firms up; an early estimate and a later one are not rival claims.",
    "consolidation": "A parent company's own accounts and the group's accounts are both correct, and different.",
    "price_basis": "Values at constant and current prices answer different questions.",
    "measure": "These are different aggregates and are not expected to match.",
}


def _fact_line(f: dict) -> str:
    bits = [f["entity_raw"], f["metric_raw"]]
    if f.get("period_raw"):
        bits.append(f["period_raw"])
    import json
    for k, v in (json.loads(f.get("basis_json") or "{}")).items():
        bits.append(str(v).replace("_", " "))
    value = f.get("value_raw") or f.get("value_text") or "—"
    unit = f.get("unit_raw") or ""
    return f"{' · '.join(bits)} = {value} {unit}".strip()


def _source(f: dict, ev: dict | None) -> str:
    if not ev:
        return f["doc_id"]
    return f"{ev.get('filename', ev.get('doc_id', ''))} p.{ev['page_no']}"


def explain(v: Verdict, a: dict, b: dict, ev_a: dict | None = None,
            ev_b: dict | None = None) -> str:
    src_a, src_b = _source(a, ev_a), _source(b, ev_b)
    shared = f"{a['entity_raw']} · {a['metric_raw']}"

    if v.label == CORROBORATES:
        val = a.get("value_raw") or a.get("value_text")
        other = b.get("value_raw") or b.get("value_text")
        same_words = str(val).strip() == str(other).strip()
        lead = (
            f"Both documents state {shared} for {a.get('period_raw') or 'the same period'}"
        )
        if same_words:
            return (f"{lead} as {val} {a.get('unit_raw') or ''}. "
                    f"Corroborated across {src_a} and {src_b}.").replace("  ", " ")
        return (f"{lead}. {src_a} writes it {val} {a.get('unit_raw') or ''} and "
                f"{src_b} writes it {other} {b.get('unit_raw') or ''}; normalized to a common "
                f"unit these are the same figure, so the two sources corroborate each other."
                ).replace("  ", " ")

    if v.label == CONTRADICTS:
        delta = f" They differ by {v.value_delta:g}." if v.value_delta else ""
        return (
            f"{src_a} and {src_b} make the same claim about {shared} for "
            f"{a.get('period_raw')} — same unit, same basis — but state different values: "
            f"{a.get('value_raw') or a.get('value_text')} against "
            f"{b.get('value_raw') or b.get('value_text')}.{delta} "
            f"Nothing in either document explains the gap, so this is recorded as a genuine "
            f"disagreement."
        )

    if v.label == RECONCILED:
        dim = v.dimension or "context"
        phrase = DIMENSION_PHRASE.get(dim, f"they differ on {dim}")
        detail = next((c.detail for c in v.checks if c.dimension == dim), "")
        hint = RECONCILE_HINT.get(dim, "")
        return (
            f"{src_a} and {src_b} both report {shared}, and every part of the claim matches "
            f"except one: {phrase} ({detail}). This is not a contradiction. {hint}"
        ).strip()

    if v.label == INSUFFICIENT:
        dim = v.dimension or "the claim frame"
        unknown = next((c for c in v.checks if c.status == "unknown"), None)
        if unknown:
            return (
                f"Both documents appear to report {shared}, but the comparison cannot be "
                f"completed: {unknown.detail}. Reported as insufficient evidence rather than "
                f"as agreement or contradiction."
            )
        return (
            f"Both documents appear to report {shared}, but {basis_mod.HUMAN.get(dim, dim)} "
            f"could not be established from the text, so no relationship is asserted."
        )

    if v.label == UNRELATED:
        return f"Not comparable: {v.dimension} differs."
    return ""


def short_headline(v: Verdict) -> str:
    if v.label == RECONCILED:
        return f"Reconciled — {basis_mod.HUMAN.get(v.dimension, v.dimension)}"
    if v.label == INSUFFICIENT:
        return "Insufficient evidence"
    return v.label.replace("_", " ").title()
