"""Entity key normalization.

Deliberately conservative: this collapses spelling and legal-suffix variants
only. It will never decide that two differently-named organisations are the
same -- that judgement goes to the adjudicator, which has to justify itself.
"""
from __future__ import annotations

import re
import unicodedata

LEGAL_SUFFIXES = {
    "limited", "ltd", "llp", "llc", "inc", "incorporated", "corp", "corporation",
    "plc", "pvt", "private", "company", "co", "gmbh", "sa", "nv", "bv", "ag",
}

HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "prof", "shri", "smt", "sri", "mx"}

# Entities that name nothing on their own. Filings are written in the first
# person -- "our Company", "the Group", "the Board" -- and the extractor
# reproduces that even when told not to. Left alone these are catastrophic for
# blocking: every "company" fact in every document lands in one block and gets
# compared against every other, which is how a system starts reporting that
# two unrelated firms contradict each other.
#
# They are rejected at ingest with reason `unresolvable_entity` and counted.
# Resolving them to the document's subject is the right fix and is listed as
# a next step; guessing is not.
GENERIC = {
    "the", "a", "an", "this", "that", "these", "those",
    "we", "us", "our", "it", "its", "they", "them", "their",
    "company", "group", "board", "management", "board of directors",
    "entity", "organisation", "organization", "firm", "business", "issuer",
    "bank", "government", "state", "country", "sector", "industry", "market",
    "subsidiary", "parent", "holding", "auditor", "auditors", "shareholders",
    "members", "committee", "authority", "regulator",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def entity_key(raw: str | None) -> str:
    """'Delhivery Limited' -> 'delhivery'; 'Mr. Suvir Suren Sujan' -> 'suvir suren sujan'."""
    if not raw:
        return ""
    s = _strip_accents(str(raw)).lower()
    s = re.sub(r"\([^)]*\)", " ", s)          # drop parentheticals
    s = re.sub(r"[^\w\s&]", " ", s)            # punctuation -> space
    s = re.sub(r"\s+", " ", s).strip()
    parts = [p for p in s.split(" ") if p]
    while parts and parts[0] in HONORIFICS:
        parts.pop(0)
    while parts and parts[-1] in LEGAL_SUFFIXES:
        parts.pop()
    return " ".join(parts) or s


def is_resolvable(key: str) -> bool:
    """Does this key name something in particular?

    A key is unresolvable when it is generic on its own, or when stripping the
    generic words from it leaves nothing -- "the Company", "our Group".
    """
    if not key or len(key) < 2:
        return False
    if key in GENERIC:
        return False
    remainder = [w for w in key.split() if w not in GENERIC]
    return bool(remainder)


def same_entity(a: str, b: str) -> tuple[bool, str]:
    """Deterministic verdict only. Returns (same, reason); callers escalate
    an 'unclear' to the adjudicator rather than guessing."""
    if not a or not b:
        return False, "missing entity"
    if a == b:
        return True, f"{a} ≡ {b}"
    ta, tb = set(a.split()), set(b.split())
    if ta and tb and (ta <= tb or tb <= ta):
        return True, f"{a} ≡ {b} (one name is contained in the other)"
    return False, f"{a} ≠ {b}"
