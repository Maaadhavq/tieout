"""PDF reading. Text, word geometry, and page rendering -- all deterministic.

Word bounding boxes are pulled on the same pass as the text so that a quote
located in the text stream can be drawn back onto the rendered page. That is
what makes evidence inspectable rather than merely cited.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

RENDER_DPI = 132


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    start: int  # char offset into Page.text
    end: int


@dataclass
class Page:
    number: int          # 1-based
    label: str
    text: str
    words: list[Word]
    width: float
    height: float

    @property
    def norm_text(self) -> str:
        return normalize_ws(self.text)


def normalize_ws(s: str) -> str:
    """Collapse the whitespace PDFs scatter through extracted text.

    Ligatures and the several dash and quote characters publishers use are
    folded too, so a model that retypes a quote with a plain hyphen still
    matches the source.
    """
    s = (s.replace("­", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
           .replace("‘", "'").replace("’", "'")
           .replace("“", '"').replace("”", '"')
           .replace("–", "-").replace("—", "-").replace("−", "-")
           .replace(" ", " ").replace(" ", " ").replace(" ", " "))
    return re.sub(r"\s+", " ", s).strip()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_pages(path: str | Path) -> list[Page]:
    doc = fitz.open(path)
    pages: list[Page] = []
    try:
        for i, pg in enumerate(doc, start=1):
            raw_words = pg.get_text("words")  # x0,y0,x1,y1,word,block,line,word_no
            text_parts: list[str] = []
            words: list[Word] = []
            cursor = 0
            # Rebuild a text stream we control the offsets of, in reading order.
            raw_words.sort(key=lambda w: (w[5], w[6], w[7]))
            for x0, y0, x1, y1, w, *_ in raw_words:
                if cursor:
                    text_parts.append(" ")
                    cursor += 1
                words.append(Word(w, x0, y0, x1, y1, cursor, cursor + len(w)))
                text_parts.append(w)
                cursor += len(w)
            pages.append(
                Page(
                    number=i,
                    label=pg.get_label() or str(i),
                    text="".join(text_parts),
                    words=words,
                    width=pg.rect.width,
                    height=pg.rect.height,
                )
            )
    finally:
        doc.close()
    return pages


def render_page_png(path: str | Path, page_no: int, dpi: int = RENDER_DPI) -> bytes:
    doc = fitz.open(path)
    try:
        pix = doc[page_no - 1].get_pixmap(dpi=dpi)
        return pix.tobytes("png")
    finally:
        doc.close()


def scale_for(page: Page, dpi: int = RENDER_DPI) -> float:
    """PDF points -> rendered pixels."""
    return dpi / 72.0
