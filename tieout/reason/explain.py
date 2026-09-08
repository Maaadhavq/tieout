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
    # The extractor files anything that narrows a figure's scope under
    # `geography` -- a region, but also a population or a venue. Saying "areas"
    # produced "they cover different areas (employees vs workers)".
    "geography": "they cover different populations or areas",
    "adjustment": "one is adjusted and the other is not",
    "unit": "they are stated in different units",
    "metric": "it is unclear whether they measure the same thing",
    "sign_convention": "the same magnitude is written with opposite signs",
    "valuation": "they use different valuation conventions",
}

RECONCILE_HINT = {
    "period": "A figure for part of a year is not in conflict with the figure for the whole year.",
    "vintage": "Institutional publishers restate the same period as data firms up; an early estimate and a later one are not rival claims.",
    "consolidation": "A parent company's own accounts and the group's accounts are both correct, and different.",
    "price_basis": "Values at constant and current prices answer different questions.",
    "measure": "These are different aggregates and are not expected to match.",
    "sign_convention": "Filings write a loss as a positive number in the narrative and in parentheses in the statements.",
    "valuation": "Basic and market prices differ by product taxes and subsidies.",
    "geography": "Two populations or regions measured the same way are not rival claims about one of them.",
}


def _source(f: dict, ev: dict | None) -> str:
    if not ev:
        return f["doc_id"]
    return f"{ev.get('filename', ev.get('doc_id', ''))} p.{ev['page_no']}"


_SYMBOL = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


def _group(raw: str) -> str:
    """Add thousands separators when the source did not, so a long figure is
    readable next to one that was already grouped."""
    if "," in raw or not raw:
        return raw
    try:
        whole, _, frac = raw.partition(".")
        if not whole.lstrip("-+").isdigit() or abs(int(whole)) < 10000:
            return raw
        return f"{int(whole):,}" + (f".{frac}" if frac else "")
    except (ValueError, TypeError):
        return raw


def _delta(v, a: dict) -> str:
    if not v.value_delta:
        return ""
    unit = "percentage points" if a.get("unit_canon") == "percent" else ""
    mag = a.get("magnitude_raw")
    shown = v.value_delta
    if unit:
        return f" They differ by {shown:g} {unit}."
    if mag:
        from ..normalize.units import MAGNITUDES
        shown = v.value_delta / MAGNITUDES.get(mag, 1)
        return f" They differ by {_group(f'{shown:,.10g}')} {mag}."
    return f" They differ by {shown:,.10g}."


def _val(f: dict) -> str:
    """Render a value the way its document wrote it, magnitude included.

    Without this a reader sees "81415.38 INR" and has no idea it is the same
    figure as "8142 crore" -- which is exactly the comparison being explained.
    """
    raw = f.get("value_raw")
    if raw is None:
        return str(f.get("value_text") or "—")
    raw = _group(str(raw))
    unit = f.get("unit_canon")
    if unit == "percent":
        return f"{raw}%"
    sym = _SYMBOL.get(unit, "")
    mag = f.get("magnitude_raw")
    if sym and mag:
        return f"{sym}{raw} {mag}"
    if sym:
        return f"{sym}{raw}"
    return f"{raw} {mag}".strip() if mag else str(raw)


def _sources(a: dict, b: dict, ev_a: dict | None, ev_b: dict | None) -> tuple[str, bool]:
    """Two documents, or one document twice? The phrasing differs."""
    sa, sb = _source(a, ev_a), _source(b, ev_b)
    if sa == sb:
        return sa, True
    return f"{sa} and {sb}", False


def _metric_phrase(a: dict, b: dict) -> str:
    """Name both metrics when the labels differ, so the reader is not told two
    facts 'both report real GDP growth' when one of them reports GVA."""
    if a["metric_raw"].strip().lower() == b["metric_raw"].strip().lower():
        return f"{a['entity_raw']} · {a['metric_raw']}"
    return f"{a['entity_raw']} · {a['metric_raw']} against {b['metric_raw']}"


def explain(v: Verdict, a: dict, b: dict, ev_a: dict | None = None,
            ev_b: dict | None = None) -> str:
    src_a, src_b = _source(a, ev_a), _source(b, ev_b)
    where, same_place = _sources(a, b, ev_a, ev_b)
    shared = _metric_phrase(a, b)
    both = "Both statements" if same_place else "Both documents"
    va, vb = _val(a), _val(b)

    if v.label == CORROBORATES:
        lead = f"{both} state {shared} for {a.get('period_raw') or 'the same period'}"
        if va == vb:
            return f"{lead} as {va}. Corroborated across {where}."
        return (
            f"{lead}. {src_a} writes it {va} and {src_b} writes it {vb}; normalized to a "
            f"common unit those are the same figure, so the two sources corroborate "
            f"each other."
        )

    if v.label == CONTRADICTS:
        delta = _delta(v, a)
        caveat = next((c.detail for c in v.checks if c.dimension == "provenance"), None)
        if caveat:
            return (
                f"{where} states {shared} for {a.get('period_raw')} as {va} in one place and "
                f"{vb} in another.{delta} Reported with low confidence: {caveat}. A row header "
                f"the extractor did not capture is the more likely explanation than the "
                f"document disagreeing with itself."
            )
        return (
            f"{where} make the same claim about {shared} for {a.get('period_raw')} — same "
            f"unit, same basis — but state different values: {va} against {vb}.{delta} "
            f"Nothing in either document explains the gap, so this is recorded as a genuine "
            f"disagreement."
        )

    if v.label == RECONCILED:
        dim = v.dimension or "context"
        phrase = DIMENSION_PHRASE.get(dim, f"they differ on {dim}")
        detail = next((c.detail for c in v.checks if c.dimension == dim), "")
        hint = RECONCILE_HINT.get(dim, "")
        lead = (f"{where} reports {shared} twice ({va} and {vb})" if same_place
                else f"{where} both report {shared} ({va} and {vb})")
        return (
            f"{lead}, and every part of the claim matches except one: {phrase} "
            f"({detail}). This is not a contradiction. {hint}"
        ).strip()

    if v.label == INSUFFICIENT:
        dim = v.dimension or "the claim frame"
        unknown = next((c for c in v.checks if c.status == "unknown"), None)
        if unknown:
            return (
                f"{both} appear to report {shared}, but the comparison cannot be completed: "
                f"{unknown.detail}. Reported as insufficient evidence rather than as "
                f"agreement or contradiction."
            )
        return (
            f"{both} appear to report {shared}, but {basis_mod.HUMAN.get(dim, dim)} could "
            f"not be established from the text, so no relationship is asserted."
        )

    if v.label == UNRELATED:
        return f"Not comparable: {v.dimension} differs."
    return ""
