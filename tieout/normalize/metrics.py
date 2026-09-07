"""Metric key derivation.

Predicates are DISCOVERED, not enumerated. There is no list of known metrics
anywhere in this codebase -- a metric key is a slug derived from whatever
label the document used, so an unseen PDF about rainfall or headcount works
the same way as one about revenue.

Two normalizations happen before slugging, and neither is document-specific:
  1. the extractor is asked to return a canonical English label alongside the
     raw span, which absorbs most surface variation ("real gross domestic
     product (GDP) growth" -> "real GDP growth");
  2. a small set of *linguistic* rules below strips filler and expands
     parenthetical acronyms. These are English-language rules, not facts.

Anything the rules cannot settle becomes an alias question for the
adjudicator, recorded in `metric_aliases` with its rationale.
"""
from __future__ import annotations

import re

from . import basis as basis_mod

STOPWORDS = {
    "the", "a", "an", "of", "for", "in", "on", "at", "to", "and", "or",
    "total", "overall",
}

# Purely orthographic collapses.
SPELLING = [
    (r"\bper\s+cent\b", "percent"),
    (r"\by[-\s]?o[-\s]?y\b", "yoy"),
    (r"\bgrowth\s+rate\b", "growth"),
    (r"\brate\s+of\s+growth\b", "growth"),
    (r"\bnumber\s+of\b", "count"),
    (r"\bno\.\s+of\b", "count"),
]

_PARENS = re.compile(r"\(([^)]*)\)")


def metric_key(label: str | None) -> str:
    """'Revenue from Operations' -> 'revenue_from_operations'.

    Parenthetical acronyms are folded into the head term when they look like
    an abbreviation of the words immediately before them, so
    'gross value added (GVA)' and 'GVA' slug identically.
    """
    if not label:
        return ""
    s = str(label).lower().strip()

    s = _fold_acronyms(s)
    for pat, rep in SPELLING:
        s = re.sub(pat, rep, s)
    s = re.sub(r"[^\w\s]", " ", s)
    words = [
        w for w in s.split()
        if w and w not in STOPWORDS and w not in basis_mod.ABSORBED
    ]
    return "_".join(words)[:120] or "unknown"


def _fold_acronyms(s: str) -> str:
    """'gross domestic product (gdp) growth' -> 'gdp growth'.

    An acronym in parentheses replaces its expansion when it spells the
    initials of the words immediately before it. Publishers write the long
    form once and the acronym thereafter, so folding them makes the two
    mentions slug identically.
    """
    def _sub(m: re.Match) -> str:
        acro = re.sub(r"[^a-z]", "", m.group(1).lower())
        if not 2 <= len(acro) <= 6:
            return " "
        before = s[:m.start()].split()
        tail = before[-len(acro):]
        if len(tail) == len(acro) and "".join(w[0] for w in tail if w) == acro:
            return f"\x00{acro} "  # marker: drop the len(acro) preceding words
        return f" {acro} "

    out = _PARENS.sub(_sub, s)
    while "\x00" in out:
        i = out.index("\x00")
        head = out[:i].split()
        acro_len = len(out[i + 1:].split(" ", 1)[0])
        out = " ".join(head[:-acro_len]) + " " + out[i + 1:]
    return out


def token_overlap(a: str, b: str) -> float:
    """Jaccard over key tokens -- the cheap signal used to decide whether a
    pair is even worth an adjudication call."""
    ta, tb = set(a.split("_")), set(b.split("_"))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def same_metric(a: str, b: str, aliases: dict[tuple[str, str], bool] | None = None) -> tuple[bool | None, str]:
    """Returns (verdict, reason). `None` means 'rules cannot settle this' and
    the caller should escalate to the adjudicator."""
    if not a or not b:
        return False, "missing metric"
    if a == b:
        return True, a
    aliases = aliases or {}
    for pair in ((a, b), (b, a)):
        if pair in aliases:
            return aliases[pair], f"alias: {a} ~ {b}"
    ov = token_overlap(a, b)
    if ov == 0.0:
        return False, f"{a} ≠ {b}"
    if ov >= 0.8:
        return True, f"{a} ≈ {b} (token overlap {ov:.2f})"
    return None, f"{a} ? {b} (token overlap {ov:.2f}) — needs adjudication"
