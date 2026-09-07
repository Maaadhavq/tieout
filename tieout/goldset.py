"""Load tests/golden/cases.yaml into store-shaped fact dicts.

Shared by the test suite and the eval harness so both score the same objects
the API serves. The gold facts were located by hand in the PDFs before any
extractor existed; normalization here runs the real production code, so a
regression in units or periods fails these tests rather than hiding.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .normalize import basis as basis_mod
from .normalize.entities import entity_key
from .normalize.metrics import metric_key
from .normalize.periods import parse_period
from .normalize.units import parse_quantity

GOLD_PATH = Path(__file__).resolve().parents[1] / "tests" / "golden" / "cases.yaml"


def load_raw(path: str | Path | None = None) -> dict:
    return yaml.safe_load(Path(path or GOLD_PATH).read_text(encoding="utf-8"))


def to_fact(g: dict) -> dict:
    kind = g.get("kind", "measurement")
    qty = parse_quantity(
        str(g["value"]) if g.get("value") is not None else None,
        g.get("unit"), g.get("magnitude"),
    )
    period = parse_period(g.get("period") or g.get("effective"))
    known, leftover = basis_mod.normalize_basis(g.get("basis"), context=g.get("quote", ""))
    for dim, val in basis_mod.infer_from_label(g["metric"]).items():
        known.setdefault(dim, val)
    if g.get("status"):
        known.setdefault("status", g["status"])
    return {
        "fact_id": g["id"],
        "doc_id": g["doc"],
        "evidence_id": f"ev_{g['id']}",
        "fact_kind": kind,
        "entity_raw": g["entity"], "entity_key": entity_key(g["entity"]),
        "metric_raw": g["metric"], "metric_key": metric_key(g["metric"]),
        "value_raw": str(g["value"]) if g.get("value") is not None else None,
        "value_num": qty.value if qty else None,
        "value_text": g.get("value_text") or g.get("status"),
        "value_tol": qty.tolerance if qty else None,
        "unit_raw": g.get("unit"), "unit_canon": qty.unit if qty else None,
        "magnitude_raw": qty.magnitude if qty else g.get("magnitude"),
        "period_raw": g.get("period") or g.get("effective"),
        "period_start": period.start.isoformat() if period else None,
        "period_end": period.end.isoformat() if period else None,
        "period_grain": period.grain if period else "unknown",
        "basis_json": json.dumps(known),
        "qualifiers_json": json.dumps(leftover),
        "confidence": 1.0,
        "page_no": g["page"],
        "quote": g["quote"],
    }


def load_facts(path: str | Path | None = None) -> dict[str, dict]:
    return {g["id"]: to_fact(g) for g in load_raw(path)["facts"]}


def load_pairs(path: str | Path | None = None) -> list[tuple[str, str, str, str]]:
    return [tuple(p) for p in load_raw(path)["pairs"]]
