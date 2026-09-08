"""LLM adjudication -- used only where the rules genuinely cannot decide.

The comparator returns `needs_adjudication` for exactly one kind of question:
do two metric labels name the same measured quantity? Everything else -- unit
conversion, period arithmetic, basis comparison, value agreement -- is
decidable from the normalized fields and is never sent to a model.

Three properties keep this honest:

  * the model answers a NARROW question ("are these the same quantity?"),
    never "what is the relationship between these facts";
  * the answer is cached in `metric_aliases` with its rationale, so one call
    settles a whole block and a wrong alias can be deleted without touching
    any fact;
  * there is a hard cap. Past it, pairs stay INSUFFICIENT_EVIDENCE, which is
    a truthful answer rather than a degraded one.
"""
from __future__ import annotations

import json
import os

from ..ingest.extract import MODEL_CHAIN

MAX_ADJUDICATIONS = int(os.environ.get("TIEOUT_MAX_ADJUDICATIONS", "40"))

SYSTEM = """You settle one narrow question about financial and statistical terminology.

Given two metric labels taken from different documents about the same entity,
say whether they name the SAME measured quantity, such that a figure reported
under one label could be compared directly against a figure reported under the
other.

Answer true only if the quantities are interchangeable. Answer false when they
are related but not the same -- a subtotal and its total, a gross and a net
figure, one segment and the whole business, two different aggregates.

Reply with JSON: {"same": bool, "confidence": 0-1, "rationale": "one sentence"}"""

MAX_LABEL_CHARS = 120


def _clean_label(label: str) -> str:
    """Untrusted text from a PDF, on its way into a prompt."""
    # Whitespace becomes a space BEFORE non-printables are dropped: a tab is
    # not printable, so stripping first welds "from	operations" into one word.
    flat = "".join(" " if ch.isspace() else ch
                   for ch in str(label or "") if ch.isspace() or ch.isprintable())
    return " ".join(flat.split())[:MAX_LABEL_CHARS]


SCHEMA = {
    "type": "object",
    "properties": {
        "same": {"type": "boolean"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["same", "confidence", "rationale"],
}


class Adjudicator:
    def __init__(self, store, model: str | None = None, api_key: str | None = None,
                 offline: bool = False, budget: int = MAX_ADJUDICATIONS):
        self.store = store
        # Share the extractor's model chain. This defaulted to a hard-coded
        # "gemini-2.5-flash", which returns 404 NOT_FOUND on a key issued after
        # that alias was retired -- so every adjudication raised, the exception
        # below turned it into "unresolved", and the component silently never
        # ran. `metric_aliases` stayed empty and /api/stats reported
        # "relationships decided by a model: 0", which reads like the rules
        # settled everything rather than like a dead dependency.
        chain = [model or os.environ.get("TIEOUT_MODEL") or MODEL_CHAIN[0]]
        chain += [m for m in MODEL_CHAIN if m != chain[0]]
        self._chain = chain
        self.model = chain[0]
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY") or ""
        self.offline = offline or not self._api_key
        self.budget = budget
        self.calls = 0
        self.last_error: str | None = None
        self._client = None
        self._cache: dict[tuple[str, str], bool] = {}
        self.load_aliases()

    def load_aliases(self) -> dict[tuple[str, str], bool]:
        self._cache = {
            (r["key_a"], r["key_b"]): bool(r["same"])
            for r in self.store.q("SELECT key_a, key_b, same FROM metric_aliases")
        }
        return self._cache

    @property
    def aliases(self) -> dict[tuple[str, str], bool]:
        return self._cache

    @property
    def client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _record(self, a: str, b: str, same: bool, rationale: str, decided_by: str) -> None:
        key = tuple(sorted((a, b)))
        self.store.insert("metric_aliases", {
            "key_a": key[0], "key_b": key[1], "same": int(same),
            "rationale": rationale, "decided_by": decided_by,
        })
        self._cache[key] = same
        self._cache[(key[1], key[0])] = same

    def resolve(self, a_key: str, a_label: str, b_key: str, b_label: str) -> bool | None:
        """Returns True/False, or None when unresolved (offline or over budget)."""
        key = tuple(sorted((a_key, b_key)))
        if key in self._cache:
            return self._cache[key]
        if self.offline or self.calls >= self.budget:
            return None

        from google.genai import types

        prompt = f'A: "{a_label}"\nB: "{b_label}"'
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM,
            response_mime_type="application/json",
            response_schema=SCHEMA,
            temperature=0.0,
        )
        # Walk the chain the way the extractor does: an unavailable model is a
        # deployment fact, not an answer about these two labels.
        data = None
        for candidate in list(self._chain):
            try:
                resp = self.client.models.generate_content(
                    model=candidate, contents=prompt, config=config)
                data = json.loads(resp.text or "{}")
                self.model = candidate
                self.last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - unresolved is a valid outcome
                self.last_error = f"{candidate}: {type(exc).__name__}: {exc}"[:200]
                if candidate in self._chain:
                    self._chain.remove(candidate)
        if data is None:
            return None

        self.calls += 1
        same = bool(data.get("same"))
        conf = float(data.get("confidence") or 0)
        rationale = (data.get("rationale") or "").strip()
        if conf < 0.6:
            # A hedged answer is not an answer; leave the pair unresolved.
            return None
        self._record(a_key, b_key, same, rationale, "llm")
        return same

    def rationale_for(self, a_key: str, b_key: str) -> str | None:
        key = tuple(sorted((a_key, b_key)))
        row = self.store.one(
            "SELECT rationale FROM metric_aliases WHERE key_a = ? AND key_b = ?", key
        )
        return row["rationale"] if row else None
