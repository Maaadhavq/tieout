-- Tie-Out fact store.
-- Nothing here names a company, a metric, or a document. Predicates are
-- discovered at ingest and interned in `metrics`; see docs/DECISIONS.md D3.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    path         TEXT,
    sha256       TEXT NOT NULL UNIQUE,
    page_count   INTEGER NOT NULL,
    publisher    TEXT,
    doc_type     TEXT,
    as_of_date   TEXT,
    ingested_at  TEXT NOT NULL,
    pages_scanned INTEGER DEFAULT 0,
    pages_skipped INTEGER DEFAULT 0
);

-- Evidence is written before the fact that cites it: a fact cannot exist
-- without a located quote (the grounding gate, DECISIONS.md D2).
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id  TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    page_no      INTEGER NOT NULL,
    quote        TEXT NOT NULL,
    char_start   INTEGER,
    char_end     INTEGER,
    bbox_json    TEXT,             -- [[x0,y0,x1,y1], ...] one rect per line of the quote
    match_type   TEXT NOT NULL     -- exact | normalized | fuzzy | failed
);

CREATE TABLE IF NOT EXISTS metrics (
    metric_key   TEXT PRIMARY KEY,
    label        TEXT NOT NULL,    -- first label seen for this key
    n_facts      INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL
);

-- Alias edges between metric keys, decided by the adjudicator. Kept separate
-- from `metrics` so a wrong alias can be dropped without touching the facts.
CREATE TABLE IF NOT EXISTS metric_aliases (
    key_a        TEXT NOT NULL,
    key_b        TEXT NOT NULL,
    same         INTEGER NOT NULL, -- 1 same metric, 0 explicitly different
    rationale    TEXT,
    decided_by   TEXT NOT NULL,    -- rule | llm
    PRIMARY KEY (key_a, key_b)
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id        TEXT PRIMARY KEY,
    doc_id         TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    evidence_id    TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
    fact_kind      TEXT NOT NULL,        -- measurement | state | event

    entity_raw     TEXT NOT NULL,
    entity_key     TEXT NOT NULL,
    metric_raw     TEXT NOT NULL,
    metric_key     TEXT NOT NULL,

    value_raw      TEXT,
    value_num      REAL,                 -- canonical magnitude-resolved number
    value_text     TEXT,                 -- categorical / state value
    value_tol      REAL,                 -- half-ulp of the stated precision

    unit_raw       TEXT,
    unit_canon     TEXT,                 -- INR | USD | percent | count | ...
    magnitude_raw  TEXT,                 -- crore | million | ...

    period_raw     TEXT,
    period_start   TEXT,                 -- ISO date
    period_end     TEXT,
    period_grain   TEXT,                 -- annual | quarterly | monthly | instant | unknown

    basis_json     TEXT NOT NULL DEFAULT '{}',
    qualifiers_json TEXT NOT NULL DEFAULT '{}',

    extraction_model TEXT,
    extraction_conf  REAL,
    grounding_conf   REAL,
    frame_completeness REAL,
    confidence       REAL,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facts_block ON facts(entity_key, metric_key);
CREATE INDEX IF NOT EXISTS idx_facts_doc   ON facts(doc_id);

-- Facts the grounding gate refused. Kept, not discarded: this table is the
-- hallucination rate reported in /stats and the README.
CREATE TABLE IF NOT EXISTS rejects (
    reject_id    TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    page_no      INTEGER,
    reason       TEXT NOT NULL,     -- quote_not_found | no_quote | unparseable_value | no_entity
    payload_json TEXT NOT NULL,     -- what the model actually emitted
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationships (
    rel_id       TEXT PRIMARY KEY,
    fact_a       TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    fact_b       TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    label        TEXT NOT NULL,     -- CORROBORATES | CONTRADICTS | CONTEXTUALLY_RECONCILED | INSUFFICIENT_EVIDENCE
    dimension    TEXT,              -- which frame dimension differed, when reconciled
    comparability_json TEXT NOT NULL,
    rule_trace_json    TEXT NOT NULL,
    value_delta  REAL,
    confidence   REAL NOT NULL,
    decided_by   TEXT NOT NULL,     -- rule | llm
    explanation  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE (fact_a, fact_b)
);

CREATE INDEX IF NOT EXISTS idx_rel_label ON relationships(label);
CREATE INDEX IF NOT EXISTS idx_rel_a ON relationships(fact_a);
CREATE INDEX IF NOT EXISTS idx_rel_b ON relationships(fact_b);

-- Content-addressed cache of model output, so re-ingesting a document costs
-- nothing and the demo runs with no API key.
CREATE TABLE IF NOT EXISTS extract_cache (
    cache_key    TEXT PRIMARY KEY,  -- sha256(page text) + prompt version + model
    response_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
