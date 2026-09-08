"""Value, unit and magnitude normalization, plus precision-aware tolerance.

Two figures agree when they are within the precision their *sources* claimed.
"8,142 crore" and "81,415.38 million" are the same number written by two
departments; "6.4 per cent" and "6.5 per cent" are not, even though the gap
is smaller in absolute terms. Tolerance therefore comes from how the number
was written, never from a fixed epsilon.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAGNITUDES = {
    "hundred": 1e2, "thousand": 1e3, "k": 1e3,
    "lakh": 1e5, "lac": 1e5, "lakhs": 1e5,
    "million": 1e6, "mn": 1e6, "mln": 1e6, "m": 1e6,
    "crore": 1e7, "crores": 1e7, "cr": 1e7,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "trillion": 1e12, "tn": 1e12, "trn": 1e12,
}

CURRENCY = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "rupees": "INR", "rupee": "INR",
    "$": "USD", "us$": "USD", "usd": "USD", "dollars": "USD", "dollar": "USD",
    "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
}

PERCENTISH = {
    "%": ("percent", 1.0), "percent": ("percent", 1.0), "per cent": ("percent", 1.0),
    "percentage": ("percent", 1.0),
    "percentage point": ("percent", 1.0), "percentage points": ("percent", 1.0),
    "pp": ("percent", 1.0), "ppt": ("percent", 1.0),
    "bps": ("percent", 0.01), "basis point": ("percent", 0.01), "basis points": ("percent", 0.01),
}


@dataclass(frozen=True)
class Quantity:
    value: float           # canonical: magnitude applied, bps folded to percent
    tolerance: float       # half-ulp of the stated precision, in canonical units
    unit: str              # INR | USD | percent | count | unknown
    magnitude: str | None  # the word actually used, kept for display
    raw: str

    def as_dict(self) -> dict:
        return {
            "value": self.value, "tolerance": self.tolerance,
            "unit": self.unit, "magnitude": self.magnitude, "raw": self.raw,
        }


_NUM = re.compile(r"[-+]?\(?\d[\d,\s]*(?:\.\d+)?\)?")

# "... per one million-person hours": the scale word belongs to the rate's
# denominator. Matched against the text immediately before a magnitude word.
_PER_TAIL = re.compile(r"\bper\s+(?:one\s+|a\s+)?$")

# Words that describe scale or shape rather than the quantity itself; they must
# not become the unit.
_NOT_A_UNIT = set(MAGNITUDES) | {
    "of", "per", "the", "a", "an", "and", "or", "in", "at", "approximately",
    "about", "around", "over", "under", "nearly", "roughly", "no", "nos",
}


# Spelling variants of ONE unit. Nothing here converts between units -- "mm"
# and "millimetres" are the same thing written twice, whereas GWh and TWh are
# not, and no factor for the second pair is ever in evidence.
UNIT_SYNONYMS = {
    "millimetre": "mm", "millimeter": "mm",
    "centimetre": "cm", "centimeter": "cm",
    "kilometre": "km", "kilometer": "km",
    "metre": "m_len", "meter": "m_len",          # "m" alone reads as a magnitude
    "kilogram": "kg", "kilogramme": "kg", "kgs": "kg",
    "ton": "tonne", "tons": "tonne", "mt": "tonne",
    "litre": "litre", "liter": "litre",
    "person": "person", "people": "person", "persons": "person",
    "employee": "person", "headcount": "person",
    "pct": "percent", "percentagepoint": "percent",
}


def _singular(token: str) -> str:
    """Conservative de-pluralisation: long words only, and never -ss/-us/-is."""
    if len(token) > 4 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _unit_token(*sources: str | None) -> str | None:
    """The unit as the document wrote it, reduced to one comparable token.

    'Mn Tons' -> 'tons';  '₹ crore' handled earlier as a currency;
    'GWh' -> 'gwh'. Returns None when nothing unit-like was written.
    """
    for src in sources:
        for word in re.findall(r"[A-Za-z]+", str(src or "")):
            token = word.lower()
            if token in _NOT_A_UNIT or len(token) > 24:
                continue
            if token in UNIT_SYNONYMS:
                return UNIT_SYNONYMS[token]
            token = _singular(token)
            return UNIT_SYNONYMS.get(token, token)
    return None


def _decimals(numeric_token: str) -> int:
    """Number of digits after the decimal point as written. '8,142' -> 0."""
    t = numeric_token.replace(",", "").replace(" ", "").strip("()")
    return len(t.split(".")[1]) if "." in t else 0


def _is_magnitude_only(unit_raw: str | None) -> bool:
    """Did the extractor put nothing but a scale word in the unit field?

    "Rs. 1,000 crore" often comes back as value="1,000", unit="crore": the
    currency is in the sentence but not in any field, so the figure normalized
    to a unitless count and would not compare against the same amount written
    "Rs. 10,000 million". This is the one case where reading the quoted sentence
    for a currency is safe -- the model has told us the unit is a scale word, so
    whatever currency the sentence carries belongs to this number.
    """
    words = re.findall(r"[A-Za-z]+", str(unit_raw or "").lower())
    return bool(words) and all(w in _NOT_A_UNIT for w in words)


def parse_quantity(value_raw: str | None, unit_raw: str | None = None,
                   magnitude_raw: str | None = None,
                   context: str | None = None) -> Quantity | None:
    """Parse a written figure. `unit_raw`/`magnitude_raw` are hints from the
    extractor; anything found inside `value_raw` itself wins over them.

    `context` is the verbatim quote the fact came from. It is consulted for one
    thing only -- a currency the extractor dropped -- and only when the unit
    field holds nothing but a scale word."""
    if value_raw is None:
        return None
    raw = str(value_raw).strip()
    blob = f"{raw} {unit_raw or ''} {magnitude_raw or ''}".lower()

    m = _NUM.search(raw)
    if not m:
        return None
    tok = m.group(0)
    negative = tok.strip().startswith("(") and tok.strip().endswith(")")
    try:
        num = float(tok.replace(",", "").replace(" ", "").strip("()"))
    except ValueError:
        return None
    if negative:
        num = -num

    dec = _decimals(tok)

    # unit
    unit = "unknown"
    scale = 1.0
    for token, (canon, mult) in sorted(PERCENTISH.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<![a-z]){re.escape(token)}", blob):
            unit, scale = canon, mult
            break
    else:
        for token, canon in sorted(CURRENCY.items(), key=lambda kv: -len(kv[0])):
            pat = re.escape(token) if not token[0].isalpha() else rf"\b{re.escape(token)}\b"
            if re.search(pat, blob):
                unit = canon
                break
        else:
            # Anything that is neither a currency nor a percentage keeps its own
            # written unit as the canonical one. Collapsing them all to "count"
            # made TWh and GWh -- or mm and inches, or tonnes and kg -- compare
            # as though they were the same unit, which turns a unit mismatch
            # into a false contradiction on any document that is not financial.
            # No conversion is attempted: there is no factor in evidence, which
            # is the same reason currencies are never converted.
            token = _unit_token(unit_raw, magnitude_raw, raw)
            if token is None and context and _is_magnitude_only(unit_raw):
                for cur, canon in sorted(CURRENCY.items(), key=lambda kv: -len(kv[0])):
                    pat = re.escape(cur) if not cur[0].isalpha() else rf"\b{re.escape(cur)}\b"
                    if re.search(pat, str(context).lower()):
                        token = canon
                        break
            unit = token or "count"

    # magnitude
    mag_word, mag_mult = None, 1.0
    for word, mult in sorted(MAGNITUDES.items(), key=lambda kv: -len(kv[0])):
        for m in re.finditer(rf"\b{re.escape(word)}\b", blob):
            # A magnitude after "per" scales the DENOMINATOR of a rate, not the
            # value. "0.56 per one million-person hours worked" is an injury
            # frequency of 0.56, not 560,000 of anything -- the rate is already
            # expressed per million hours, so multiplying by a million states
            # it in units nobody wrote.
            if _PER_TAIL.search(blob[:m.start()]):
                continue
            mag_word, mag_mult = word, mult
            break
        if mag_word:
            break
    if unit == "percent":
        mag_word, mag_mult = None, 1.0  # "6.5 per cent" is never 6.5 million percent

    value = num * mag_mult * scale
    # Half of the last stated decimal place, carried through the same scaling.
    tolerance = 0.5 * (10 ** -dec) * mag_mult * scale
    return Quantity(value=value, tolerance=tolerance, unit=unit,
                    magnitude=mag_word, raw=raw)


def agree(a: Quantity, b: Quantity) -> tuple[bool, float]:
    """Do two quantities state the same figure? Returns (agree, delta).

    Tolerance is the *looser* of the two, because a value rounded to the
    nearest crore cannot be held to a precision it never claimed.
    """
    delta = abs(a.value - b.value)
    return delta <= max(a.tolerance, b.tolerance), delta


def comparable_units(a: Quantity, b: Quantity) -> bool:
    """Currencies are never converted -- no FX rate is in evidence, so a
    cross-currency pair is reported as incomparable rather than guessed."""
    return a.unit == b.unit and a.unit != "unknown"


def format_value(q: Quantity) -> str:
    if q.unit == "percent":
        return f"{q.value:g}%"
    if q.magnitude:
        shown = q.value / MAGNITUDES[q.magnitude]
        sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(q.unit, "")
        return f"{sym}{shown:,.10g} {q.magnitude}"
    if q.unit in ("INR", "USD", "EUR", "GBP"):
        sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}[q.unit]
        return f"{sym}{q.value:,.10g}"
    return f"{q.value:,.10g}"
