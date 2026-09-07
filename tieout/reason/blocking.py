"""Candidate generation.

Comparing every fact against every other fact is O(n^2): the starter corpus
alone would be ~30,000 pairs, and asking a model to judge each one would be
both expensive and unexplainable.

Facts are instead grouped into BLOCKS keyed on (entity_key, metric_key), and
only facts inside a block are ever compared. The key is derived from
normalization that already ran at ingest, so blocking costs one pass and no
model calls.

Because blocks are keyed on stored columns, adding a document later means
comparing its facts against the members of the blocks they land in -- not
rebuilding anything. That is the whole of the incremental-ingest extension
(see `pairs_for_new_facts`).
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from ..normalize.metrics import token_overlap

# Near-miss metric keys inside the same entity are still worth comparing --
# that is how an alias question ever reaches the adjudicator.
NEAR_MISS_MIN = 0.34


def block_key(fact: dict) -> tuple[str, str]:
    return (fact["entity_key"], fact["metric_key"])


def build_blocks(facts: list[dict]) -> dict[tuple[str, str], list[dict]]:
    blocks: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for f in facts:
        blocks[block_key(f)].append(f)
    return blocks


def _entity_groups(facts: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in facts:
        groups[f["entity_key"]].append(f)
    return groups


def candidate_pairs(facts: list[dict], cross_document_only: bool = False) -> list[tuple[dict, dict]]:
    """All pairs worth comparing, deduplicated.

    Within an entity: every pair sharing a metric key, plus pairs whose metric
    keys are near misses (possible aliases).
    """
    pairs: list[tuple[dict, dict]] = []
    seen: set[tuple[str, str]] = set()

    for _entity, group in _entity_groups(facts).items():
        by_metric: dict[str, list[dict]] = defaultdict(list)
        for f in group:
            by_metric[f["metric_key"]].append(f)

        for members in by_metric.values():
            for a, b in combinations(members, 2):
                _add(pairs, seen, a, b, cross_document_only)

        keys = sorted(by_metric)
        for i, ka in enumerate(keys):
            for kb in keys[i + 1:]:
                if token_overlap(ka, kb) >= NEAR_MISS_MIN:
                    for a in by_metric[ka]:
                        for b in by_metric[kb]:
                            _add(pairs, seen, a, b, cross_document_only)
    return pairs


def _add(pairs, seen, a, b, cross_document_only):
    if a["fact_id"] == b["fact_id"]:
        return
    if cross_document_only and a["doc_id"] == b["doc_id"]:
        return
    key = tuple(sorted((a["fact_id"], b["fact_id"])))
    if key in seen:
        return
    seen.add(key)
    pairs.append((a, b))


def pairs_for_new_facts(new_facts: list[dict], existing: list[dict]) -> list[tuple[dict, dict]]:
    """Incremental ingest: compare new facts against the blocks they join, and
    against each other. Existing-to-existing pairs are never recomputed."""
    pairs = candidate_pairs(new_facts)
    seen = {tuple(sorted((a["fact_id"], b["fact_id"]))) for a, b in pairs}

    old_by_entity = _entity_groups(existing)
    for f in new_facts:
        for other in old_by_entity.get(f["entity_key"], ()):
            if other["metric_key"] == f["metric_key"] or \
               token_overlap(other["metric_key"], f["metric_key"]) >= NEAR_MISS_MIN:
                _add(pairs, seen, f, other, False)
    return pairs


def stats(facts: list[dict]) -> dict:
    blocks = build_blocks(facts)
    n = len(facts)
    return {
        "facts": n,
        "blocks": len(blocks),
        "largest_block": max((len(v) for v in blocks.values()), default=0),
        "pairs_considered": len(candidate_pairs(facts)),
        "pairs_if_naive": n * (n - 1) // 2,
    }
