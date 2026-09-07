"""Fact extraction with Gemini.

This is the ONE step where a model is genuinely better than rules: turning a
sentence of financial English into fields. Everything downstream -- grounding,
normalization, comparison -- is deterministic, so a bad extraction shows up as
a rejected fact rather than a wrong answer.

Two properties matter more than extraction quality:

  1. Every fact must carry a VERBATIM quote. The prompt says so repeatedly and
     ground.py enforces it. A model that paraphrases loses the fact.
  2. Results are cached by content hash, so re-ingesting a document costs
     nothing and `--demo` replays a whole corpus with no API key.

The prompt deliberately does not name a company, a metric or a document type.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import random
import threading
import time
from dataclasses import dataclass

PROMPT_VERSION = "v3"

# Hosted models come and go, and a 500-page run will meet a 503. The extractor
# walks this list per page and drops to the next on an unavailable model, so a
# capacity spike degrades quality slightly instead of failing the ingest.
# Override the head of the list with TIEOUT_MODEL.
MODEL_CHAIN = [
    m for m in [
        os.environ.get("TIEOUT_MODEL"),
        "gemini-3.6-flash",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
    ] if m
]
DEFAULT_MODEL = MODEL_CHAIN[0]

RETRYABLE = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "internal error", "500")
MAX_ATTEMPTS_PER_MODEL = 3
# A model that is down stays down for a while. Without this, every page pays
# the full retry ladder on each dead model before reaching a live one -- about
# 21 seconds of pure sleeping per page, which is most of a long run.
DISABLE_AFTER_CONSECUTIVE_FAILURES = 3

SYSTEM = """You read one page of a document and list the checkable factual claims on it.

A claim is worth extracting when a reader could later ask "is that still true?"
or "does another document agree?" -- a measured quantity, or a stated status.
Skip: definitions, opinions, forward-looking commentary with no figure,
table-of-contents lines, page furniture, and anything you cannot quote.

For every claim return:

  fact_kind   "measurement" for a quantity; "state" for a status that holds
              over a period (a role, a listing, an approval); "event" for a
              dated occurrence.
  entity      Who or what the claim is about, as a proper name. A company, an
              institution, a country, a person. Not a pronoun.
  metric      A canonical English label for WHAT is measured or asserted, with
              acronyms kept in parentheses the first time, e.g.
              "real gross domestic product (GDP) growth", "revenue from
              operations", "board role". Do not invent units into the label.
  value       The figure exactly as written, digits and separators intact
              ("81,415.38", "6.5", "(452)"). Omit for state/event facts.
  value_text  For state/event facts, the status or role in a few words
              ("Non-Executive Director", "resigned", "approved").
  unit        The unit as written ("per cent", "Rs. million", "₹ crore", "%").
  magnitude   The scale word if one is present ("crore", "million", "billion").
  period      The reporting period exactly as the page writes it ("FY24",
              "2024-25", "FY2024/25", "Q4 FY24", "as at March 31, 2024").
              Omit if the page truly does not say.
  basis       Any qualifier that changes what the number means. Use these keys
              when they apply, and only when the page states them:
                consolidation  consolidated | standalone
                vintage        first advance estimate | provisional estimate |
                               revised estimate | projection | realized
                price_basis    constant | current | basic | market
                measure        GDP | GVA | gross | net
                geography      the region the figure covers
              A projected or forecast figure ALWAYS gets vintage "projection".
              An estimate ALWAYS gets the estimate round it belongs to.
  quote       A VERBATIM span copied from the page, character for character,
              long enough to contain the value and enough context to identify
              the claim. Copy; do not retype from memory, do not tidy spacing,
              do not translate, do not join text that is not adjacent on the
              page. If you cannot copy an exact span containing the value,
              DROP THE CLAIM ENTIRELY -- a claim without a real quote is worse
              than a missing one.

Return at most 12 claims per page: the most substantive ones. Prefer a claim
stated in a sentence over one read out of a dense table, because a table cell
you cannot quote contiguously will be rejected anyway."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact_kind": {"type": "string", "enum": ["measurement", "state", "event"]},
                    "entity": {"type": "string"},
                    "metric": {"type": "string"},
                    "value": {"type": "string"},
                    "value_text": {"type": "string"},
                    "unit": {"type": "string"},
                    "magnitude": {"type": "string"},
                    "period": {"type": "string"},
                    "basis": {
                        "type": "object",
                        "properties": {
                            "consolidation": {"type": "string"},
                            "vintage": {"type": "string"},
                            "price_basis": {"type": "string"},
                            "measure": {"type": "string"},
                            "geography": {"type": "string"},
                        },
                    },
                    "quote": {"type": "string"},
                },
                "required": ["fact_kind", "entity", "metric", "quote"],
            },
        }
    },
    "required": ["facts"],
}


@dataclass
class PageResult:
    page_no: int
    facts: list[dict]
    cached: bool
    error: str | None = None
    model: str | None = None


def cache_key(page_text: str, model: str | None = None) -> str:
    """Keyed on the prompt and the page text only, NOT the model.

    The committed cache is meant to be replayable by an evaluator who may not
    have access to the same hosted model we ran against. Which model actually
    produced an entry is recorded inside it instead.
    """
    return hashlib.sha256(
        f"{PROMPT_VERSION}\x00{page_text}".encode("utf-8")).hexdigest()


class Extractor:
    def __init__(self, store, model: str = DEFAULT_MODEL, api_key: str | None = None,
                 offline: bool = False):
        self.store = store
        self.model = model
        self.chain = [model] + [m for m in MODEL_CHAIN if m != model]
        self.offline = offline
        self._client = None
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY") or ""
        self.calls = 0
        self.cache_hits = 0
        self.model_used: dict[str, int] = {}
        self.retries = 0
        self._lock = threading.Lock()
        self._consecutive_failures: dict[str, int] = {}
        self.disabled: set[str] = set()

    def _note_failure(self, model: str) -> None:
        with self._lock:
            n = self._consecutive_failures.get(model, 0) + 1
            self._consecutive_failures[model] = n
            if n >= DISABLE_AFTER_CONSECUTIVE_FAILURES and model not in self.disabled:
                self.disabled.add(model)
                print(f"      ! {model} unavailable after {n} consecutive failures — "
                      f"skipping it for the rest of this run")

    def _note_success(self, model: str) -> None:
        with self._lock:
            self._consecutive_failures[model] = 0
            self.model_used[model] = self.model_used.get(model, 0) + 1

    def _live_chain(self) -> list[str]:
        with self._lock:
            live = [m for m in self.chain if m not in self.disabled]
        return live or self.chain[-1:]  # never disable the last resort

    @property
    def client(self):
        if self._client is None:
            if self.offline or not self._api_key:
                raise RuntimeError(
                    "No GEMINI_API_KEY and this page is not cached. "
                    "Run `python run.py --demo` to replay the committed cache."
                )
            from google import genai
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def extract_page(self, page_no: int, page_text: str) -> PageResult:
        key = cache_key(page_text)
        hit = self.store.cache_get(key)
        if hit is not None:
            self.cache_hits += 1
            return PageResult(page_no, hit.get("facts", []), cached=True)

        if self.offline:
            return PageResult(page_no, [], cached=False, error="not cached and running offline")

        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            temperature=0.0,
        )
        last = "no model attempted"

        for model in self._live_chain():
            for attempt in range(MAX_ATTEMPTS_PER_MODEL):
                try:
                    resp = self.client.models.generate_content(
                        model=model,
                        contents=f"PAGE {page_no}\n\n{page_text}",
                        config=config,
                    )
                    data = json.loads(resp.text or "{}")
                except Exception as exc:  # noqa: BLE001 - never fatal to the run
                    last = f"{type(exc).__name__}: {exc}"
                    if any(t in str(exc) for t in RETRYABLE):
                        self.retries += 1
                        if attempt == MAX_ATTEMPTS_PER_MODEL - 1:
                            self._note_failure(model)
                            break
                        time.sleep(1.5 * (2 ** attempt) + random.random())
                        continue
                    self._note_failure(model)  # 404: the model is gone entirely
                    break
                self.calls += 1
                self._note_success(model)
                data["_model"] = model
                self.store.cache_put(key, data)
                return PageResult(page_no, data.get("facts", []), cached=False, model=model)

        return PageResult(page_no, [], cached=False, error=last)

    def extract_pages(self, pages, workers: int = 6, progress=None) -> list[PageResult]:
        out: list[PageResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self.extract_page, p.number, p.text): p.number for p in pages
            }
            for fut in concurrent.futures.as_completed(futures):
                res = fut.result()
                out.append(res)
                if progress:
                    progress(res)
        return sorted(out, key=lambda r: r.page_no)
