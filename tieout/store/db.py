"""SQLite access. One connection per process, row factory returns dicts."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = Path(__file__).with_name("schema.sql")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    """One connection, guarded by a lock.

    Extraction runs pages concurrently and every worker writes to the cache, so
    the connection is shared across threads (`check_same_thread=False`). Without
    the lock, two threads interleave inside a transaction and one of them raises
    `cannot commit - no transaction is active` partway through a long ingest.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        # sqlite3 raises a bare "unable to open database file" when the parent
        # directory is missing, which sends people looking for a permissions
        # problem. Create it.
        if self.path.parent and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))

    # -- generic helpers -------------------------------------------------
    def q(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    def one(self, sql: str, args: Iterable[Any] = ()) -> dict | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def run(self, sql: str, args: Iterable[Any] = ()) -> None:
        with self._lock:
            self.conn.execute(sql, tuple(args))
            self.conn.commit()

    def insert(self, table: str, row: dict) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._lock:
            self.conn.execute(
                f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})",
                tuple(row.values()),
            )
            self.conn.commit()

    # -- domain helpers --------------------------------------------------
    def doc_by_hash(self, sha: str) -> dict | None:
        return self.one("SELECT * FROM documents WHERE sha256 = ?", (sha,))

    def intern_metric(self, key: str, label: str) -> None:
        existing = self.one("SELECT metric_key FROM metrics WHERE metric_key = ?", (key,))
        if existing:
            self.run("UPDATE metrics SET n_facts = n_facts + 1 WHERE metric_key = ?", (key,))
        else:
            self.insert(
                "metrics",
                {"metric_key": key, "label": label, "n_facts": 1, "created_at": now()},
            )

    def cache_get(self, key: str) -> Any | None:
        row = self.one("SELECT response_json FROM extract_cache WHERE cache_key = ?", (key,))
        return json.loads(row["response_json"]) if row else None

    def cache_put(self, key: str, value: Any) -> None:
        self.insert(
            "extract_cache",
            {"cache_key": key, "response_json": json.dumps(value), "created_at": now()},
        )

    def stats(self) -> dict:
        kept = self.one("SELECT COUNT(*) c FROM facts")["c"]
        rejected = self.one("SELECT COUNT(*) c FROM rejects")["c"]
        emitted = kept + rejected
        by_label = {
            r["label"]: r["c"]
            for r in self.q("SELECT label, COUNT(*) c FROM relationships GROUP BY label")
        }
        by_reason = {
            r["reason"]: r["c"] for r in self.q("SELECT reason, COUNT(*) c FROM rejects GROUP BY reason")
        }
        pages = self.one(
            "SELECT COALESCE(SUM(page_count),0) p, COALESCE(SUM(pages_scanned),0) s,"
            " COALESCE(SUM(pages_skipped),0) k FROM documents"
        )
        hand = self.one("SELECT COUNT(*) c FROM facts "
                        "WHERE extraction_model = 'hand-labelled reference set'")["c"]
        return {
            # True when the corpus is the hand-labelled reference set rather than
            # model output, in which case a hallucination rate is meaningless.
            "is_reference_set": bool(kept and hand == kept),
            "documents": self.one("SELECT COUNT(*) c FROM documents")["c"],
            "pages_total": pages["p"],
            # Candidate pages (passed the junk filter) vs pages a model
            # actually answered for. These differ when a run is cut short --
            # by a quota, a network failure, or an interrupt -- and reporting
            # only the first would overstate how much of the corpus was read.
            "pages_candidate": pages["s"],
            "pages_extracted": self.one(
                "SELECT COUNT(*) c FROM extract_cache")["c"],
            "pages_scanned": pages["s"],
            "pages_skipped": pages["k"],
            "facts_kept": kept,
            "facts_rejected": rejected,
            "facts_emitted": emitted,
            # Two different failures, deliberately not averaged together.
            # `hallucination_rate` counts ONLY claims whose quote could not be
            # found in the source -- the model asserting something the page does
            # not say. `rejection_rate` is every reason a fact was refused,
            # including well-grounded ones we could not place (an entity of
            # "our Company", a value that would not parse). Lumping them
            # together inflates the first, which is the number people read.
            "hallucination_rate": (
                round(by_reason.get("quote_not_found", 0) / emitted, 4) if emitted else 0.0),
            "rejection_rate": round(rejected / emitted, 4) if emitted else 0.0,
            "ungrounded": by_reason.get("quote_not_found", 0),
            "rejects_by_reason": by_reason,
            "relationships": sum(by_label.values()),
            "relationships_by_label": by_label,
            "metrics_discovered": self.one("SELECT COUNT(*) c FROM metrics")["c"],
            "llm_decided": self.one(
                "SELECT COUNT(*) c FROM relationships WHERE decided_by = 'llm'"
            )["c"],
        }
