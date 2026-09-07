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
import re
from dataclasses import dataclass

from .pdf import Page, Word, normalize_ws

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


def _norm_index(page_text: str) -> tuple[str, list[int]]:
    """Whitespace-folded text plus a map from folded offset -> raw offset."""
    out, idx = [], []
    prev_space = True
    for i, ch in enumerate(page_text):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
        else:
            out.append(ch)
            idx.append(i)
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

    # 2. normalized
    folded, back = _norm_index(raw)
    nfolded = normalize_ws(folded).lower()
    nquote = normalize_ws(quote).lower()
    # normalize_ws may shift offsets; rebuild a parallel simple fold instead
    simple = re.sub(r"\s+", " ", raw).lower()
    simple_map = _norm_index(raw)[1]
    pos = simple.find(nquote)
    if pos < 0:
        pos = nfolded.find(nquote)
        simple_map = back
    if pos >= 0 and pos < len(simple_map):
        s = simple_map[pos]
        e = simple_map[min(pos + len(nquote), len(simple_map)) - 1] + 1
        return Grounding("normalized", s, e, _bboxes_for_span(page, s, e),
                         CONF["normalized"], raw[s:e])

    # 3. fuzzy over a sliding window of the same length
    if len(nquote) >= MIN_QUOTE_CHARS and simple:
        best_ratio, best_pos = 0.0, -1
        step = max(1, len(nquote) // 8)
        matcher = difflib.SequenceMatcher(autojunk=False, b=nquote)
        for i in range(0, max(1, len(simple) - len(nquote)), step):
            window = simple[i:i + len(nquote)]
            matcher.set_seq1(window)
            if matcher.real_quick_ratio() < best_ratio or matcher.quick_ratio() < best_ratio:
                continue
            r = matcher.ratio()
            if r > best_ratio:
                best_ratio, best_pos = r, i
        if best_ratio >= FUZZY_MIN and best_pos >= 0 and best_pos < len(simple_map):
            s = simple_map[best_pos]
            e = simple_map[min(best_pos + len(nquote), len(simple_map)) - 1] + 1
            return Grounding("fuzzy", s, e, _bboxes_for_span(page, s, e),
                             CONF["fuzzy"] * best_ratio, raw[s:e])

    return Grounding("failed", None, None, [], 0.0)
