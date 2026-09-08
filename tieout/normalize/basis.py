"""Basis normalization -- the dimensions that turn an apparent contradiction
into an explained one.

A figure is not fully specified by entity + metric + period + unit. Two
statements can match on all four and still describe different things:

    consolidation   consolidated vs standalone
    vintage         first advance estimate vs provisional vs revised vs realized vs projection
    price_basis     constant prices vs current prices vs basic prices
    measure         GDP vs GVA, gross vs net
    geography       all-India vs a state, urban vs rural

`vintage` is the highest-value field in the schema: institutional publishers
restate the same period repeatedly as data firms up, and a system that does
not model it will report every restatement as a contradiction.

The vocabulary below is linguistic (how English writes these distinctions),
not a list of facts about any document. Unrecognised keys survive untouched
in `qualifiers`, so an unseen dimension is preserved rather than dropped.
"""
from __future__ import annotations

import re

DIMENSIONS = ("consolidation", "vintage", "price_basis", "valuation", "measure",
              "geography", "adjustment")

_VOCAB: dict[str, list[tuple[str, str]]] = {
    "consolidation": [
        (r"\bconsolidat", "consolidated"),
        (r"\bstandalone\b|\bstand[- ]alone\b|\bunconsolidated\b", "standalone"),
        (r"\bsegment\b", "segment"),
    ],
    "vintage": [
        (r"\bfirst\s+advance\b|\b1st\s+advance\b|\bfae\b", "first_advance_estimate"),
        (r"\bsecond\s+advance\b|\b2nd\s+advance\b", "second_advance_estimate"),
        (r"\badvance\s+estimate", "advance_estimate"),
        (r"\bprovisional\b|\bpe\b", "provisional_estimate"),
        (r"\bfirst\s+revised\b|\brevised\s+estimate|\bre\b", "revised_estimate"),
        (r"\bproject(ed|ion)\b|\bforecast\b|\bbaseline\b|\bexpected\b|\bestimated to\b|\boutlook\b",
         "projection"),
        (r"\bactual\b|\brealis|\breliaz|\brealiz|\bfinal\b", "realized"),
    ],
    # Inflation adjustment. Orthogonal to `valuation` below: a figure can be
    # "GVA at basic prices at constant prices", so conflating the two makes a
    # single conceptual difference look like two and buries the reconciliation.
    "price_basis": [
        (r"\bconstant\s+price|\breal\b|\b2011-12\s+price", "constant"),
        (r"\bcurrent\s+price|\bnominal\b", "current"),
    ],
    # Whether product taxes and subsidies are included.
    "valuation": [
        (r"\bbasic\s+price", "basic"),
        (r"\bmarket\s+price|\bfactor\s+cost", "market"),
    ],
    "measure": [
        (r"\bgva\b|\bgross\s+value\s+added\b", "GVA"),
        (r"\bgdp\b|\bgross\s+domestic\s+product\b", "GDP"),
        (r"\bgnp\b|\bgross\s+national\b", "GNP"),
        (r"\bnet\b(?!\s+exports)", "net"),
        (r"\bgross\b(?!\s+(domestic|value|national))", "gross"),
    ],
    "adjustment": [
        (r"\bseasonally\s+adjust", "seasonally_adjusted"),
        (r"\bannualis|\bannualiz", "annualized"),
    ],
}


def normalize_basis(raw: dict | None, context: str = "") -> tuple[dict, dict]:
    """Split whatever the extractor emitted into (known dimensions, leftovers).

    `context` is the surrounding sentence; a basis stated in prose rather than
    in a labelled field is still picked up from it.
    """
    known: dict[str, str] = {}
    leftover: dict[str, str] = {}
    raw = raw or {}

    blob = _sep(" ".join([str(v) for v in raw.values()] + [str(context or "")]))

    # Walk DIMENSIONS, not _VOCAB. Not every dimension can have a vocabulary:
    # `geography` is open-ended -- a region, a population, a venue -- so there
    # is nothing to enumerate. Walking _VOCAB skipped it entirely, and the
    # leftover pass below skips anything already in DIMENSIONS, so a stated
    # geography fell between the two and was silently dropped. A dimension with
    # no rules now still reaches the verbatim fallback further down.
    for dim in DIMENSIONS:
        rules = _VOCAB.get(dim, [])
        explicit = raw.get(dim)
        if explicit:
            text = _sep(str(explicit))
            hit = _match(text, rules)
            if hit:
                known[dim] = hit
                continue
            # The extractor filed this under the wrong dimension -- e.g. "basic
            # prices" under price_basis. Route it to whichever vocabulary knows
            # the term rather than inventing a value for the stated dimension.
            for other, other_rules in _VOCAB.items():
                other_hit = _match(text, other_rules)
                if other_hit:
                    known.setdefault(other, other_hit)
                    break
            else:
                known[dim] = _slug(str(explicit))
            continue
        hit = _match(blob, rules)
        if hit:
            known[dim] = hit

    for k, v in raw.items():
        if k not in DIMENSIONS and v not in (None, "", []):
            leftover[_slug(k)] = str(v)

    return known, leftover


def _sep(s: str) -> str:
    """Underscores and hyphens read as spaces: extractors emit both."""
    return re.sub(r"[_\-]+", " ", str(s)).lower()


def _match(text: str, rules: list[tuple[str, str]]) -> str | None:
    for pat, val in rules:
        if re.search(pat, text):
            return val
    return None


def _slug(s: str) -> str:
    return re.sub(r"[^\w]+", "_", str(s).strip().lower()).strip("_")


# Tokens that belong to a basis DIMENSION rather than to the identity of the
# metric. "real GDP growth" and "real GVA growth" measure the same thing on
# two different aggregates; keeping GDP/GVA inside the metric key would make
# them unrelated and hide the reconciliation. They are moved to `measure`
# instead -- see infer_from_label, which conserves the information.
ABSORBED = {
    "gdp": ("measure", "GDP"),
    "gva": ("measure", "GVA"),
    "gnp": ("measure", "GNP"),
    "consolidated": ("consolidation", "consolidated"),
    "standalone": ("consolidation", "standalone"),
    "unconsolidated": ("consolidation", "standalone"),
}


def infer_from_label(label: str | None) -> dict:
    """Basis dimensions stated inside the metric label itself.

    Called before the label is slugged, so nothing is lost when metric_key
    strips these tokens out.
    """
    out: dict[str, str] = {}
    if not label:
        return out
    for tok in re.findall(r"[a-z]+", str(label).lower()):
        if tok in ABSORBED:
            dim, val = ABSORBED[tok]
            out.setdefault(dim, val)
    return out


# Dimensions the extractor fills opportunistically rather than systematically.
# `geography` is open-ended and has no vocabulary, so it is the slot a model
# reaches for whenever a figure carries any extra qualifier at all -- it arrives
# on roughly 5% of facts, and on one side of a pair far more often than on both.
# A value present on one side therefore says nothing about the other document,
# and counting it as `unknown` suppresses findings: it demoted a real
# cross-document contradiction to INSUFFICIENT_EVIDENCE purely because one
# publisher restated the country the other left implicit.
#
# Ignoring a one-sided value is exactly the behaviour before this dimension was
# read at all, so nothing regresses; what is new is that when BOTH sides state
# it, the difference is now visible instead of discarded.
OPTIONAL = {"geography"}


def compare_basis(a: dict, b: dict) -> tuple[list[str], list[str]]:
    """Returns (dimensions that differ, dimensions only one side states).

    A dimension only one side states is *unknown*, not different -- that
    distinction is what separates CONTEXTUALLY_RECONCILED (we know why they
    differ) from INSUFFICIENT_EVIDENCE (we do not). Dimensions in OPTIONAL are
    exempt: stated by one side alone they are carried as context, not treated
    as a gap in the evidence.
    """
    differs, unknown = [], []
    for dim in DIMENSIONS:
        va, vb = a.get(dim), b.get(dim)
        if va and vb:
            if va != vb:
                differs.append(dim)
        elif (va or vb) and dim not in OPTIONAL:
            unknown.append(dim)
    return differs, unknown


HUMAN = {
    "consolidation": "consolidation basis",
    "vintage": "data vintage",
    "price_basis": "price basis",
    "valuation": "valuation convention",
    "measure": "measure",
    "geography": "geography",
    "adjustment": "adjustment",
}
