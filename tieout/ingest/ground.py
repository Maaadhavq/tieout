"""The grounding gate.

A model is allowed to tell us what a page says only if it can quote the page.
Every candidate fact carries a verbatim span; before the fact is stored we go
back to the PDF and look for that span. If it is not there, the fact is
rejected and counted.

This is the difference between citing a source and having one. It is also
where the hallucination rate in /stats comes from, and it is deliberately the
one step in the pipeline that no model participates in.

Match ladder, strictest first:
    exact       the quote appears verbatim in the page text
    normalized  it appears after whitespace/ligature/dash folding
    fuzzy       >= FUZZY_MIN of its tokens appear contiguously (OCR-ish drift)
    failed      -> rejected
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from .pdf import Page, Word

FUZZY_MIN = 0.90
MIN_QUOTE_CHARS = 12

CONF = {"exact": 1.0, "normalized": 0.97, "fuzzy": 0.75, "failed": 0.0}


@dataclass
class Grounding:
    match_type: str
    char_start: int | None
    char_end: int | None
    bboxes: list[list[float]]
    confidence: float
    located_text: str | None = None

    @property
    def ok(self) -> bool:
        return self.match_type != "failed"


def _bboxes_for_span(page: Page, start: int, end: int) -> list[list[float]]:
    """Rects for the words overlapping [start, end), merged per text line."""
    hits = [w for w in page.words if w.start < end and w.end > start]
    if not hits:
        return []
    lines: list[list[Word]] = []
    for w in hits:
        if lines and abs(lines[-1][-1].y0 - w.y0) < 3.0:
            lines[-1].append(w)
        else:
            lines.append([w])
    out = []
    for line in lines:
        out.append([
            min(w.x0 for w in line), min(w.y0 for w in line),
            max(w.x1 for w in line), max(w.y1 for w in line),
        ])
    return out


# The folds `normalize_ws` applies, as a table this module can walk one
# character at a time. Two of them change length -- a ligature expands to two
# letters, a soft hyphen disappears -- which is what made the old two-function
# approach wrong.
_FOLD = {
    "­": "",                                    # soft hyphen: dropped
    "ﬁ": "fi", "ﬂ": "fl",                  # ligatures: one char -> two
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
}


def _fold_index(text: str) -> tuple[str, list[int]]:
    """Folded, whitespace-collapsed, lowercased text plus folded -> raw offsets.

    One pass, so every folded offset maps to the raw character that produced
    it. This used to be two functions: the folded string came from
    `normalize_ws` and the offset map from a whitespace-only walk. They
    disagree whenever a fold changes length, so a single ligature earlier on
    the page pushed every later offset out by one and the located span -- and
    the highlight box drawn from it -- came back shifted. A soft hyphen shifted
    it the other way. Both are ordinary in typeset PDFs.
    """
    out: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        repl = _FOLD.get(ch, ch)
        if not repl:
            continue                                  # soft hyphen: no output
        if repl.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
            continue
        for c in repl.lower():
            out.append(c)
            idx.append(i)                             # both halves point at the ligature
        prev_space = False
    return "".join(out), idx


def locate(page: Page, quote: str) -> Grounding:
    if not quote or len(quote.strip()) < MIN_QUOTE_CHARS:
        return Grounding("failed", None, None, [], 0.0)

    raw = page.text
    # 1. exact
    pos = raw.find(quote)
    if pos >= 0:
        return Grounding("exact", pos, pos + len(quote),
                         _bboxes_for_span(page, pos, pos + len(quote)), CONF["exact"], quote)

    # 2. normalized. Page and quote are folded by the same walk, so an offset
    # found in one maps correctly into the other.
    folded, back = _fold_index(raw)
    nquote = _fold_index(quote)[0].strip()

    def _span(pos: int) -> tuple[int, int]:
        s = back[pos]
        e = back[min(pos + len(nquote), len(back)) - 1] + 1
        return s, e

    pos = folded.find(nquote)
    if nquote and pos >= 0 and pos < len(back):
        s, e = _span(pos)
        return Grounding("normalized", s, e, _bboxes_for_span(page, s, e),
                         CONF["normalized"], raw[s:e])

    # 3. fuzzy over a sliding window of the same length
    if len(nquote) >= MIN_QUOTE_CHARS and folded:
        best_ratio, best_pos = 0.0, -1
        step = max(1, len(nquote) // 8)
        matcher = difflib.SequenceMatcher(autojunk=False, b=nquote)
        for i in range(0, max(1, len(folded) - len(nquote)), step):
            window = folded[i:i + len(nquote)]
            matcher.set_seq1(window)
            if matcher.real_quick_ratio() < best_ratio or matcher.quick_ratio() < best_ratio:
                continue
            r = matcher.ratio()
            if r > best_ratio:
                best_ratio, best_pos = r, i
        if best_ratio >= FUZZY_MIN and best_pos >= 0 and best_pos < len(back):
            s, e = _span(best_pos)
            return Grounding("fuzzy", s, e, _bboxes_for_span(page, s, e),
                             CONF["fuzzy"] * best_ratio, raw[s:e])

    return Grounding("failed", None, None, [], 0.0)
