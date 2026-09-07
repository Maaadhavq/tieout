# Tie-Out — a fact knowledge layer with an audit trail

Extracts checkable claims from PDFs, refuses any it cannot quote, and works out
whether two claims corroborate each other, contradict each other, or only look
like they disagree.

![The evidence viewer: fact ledger, comparability trace, and the source page with the quote highlighted](docs/screenshot.png)

The idea the whole system rests on:

> **Comparability is a separate, prior question to agreement.**

Every fact carries a **claim frame** — entity, metric, unit, period, basis. The
comparator walks those five dimensions *before* it looks at a single value. What
the walk finds decides the label, and the walk itself is what you see on screen:

| frame | values | label |
|---|---|---|
| identical | agree within tolerance | `CORROBORATES` |
| identical | differ beyond tolerance | `CONTRADICTS` |
| differs on exactly one dimension | irrelevant | `CONTEXTUALLY_RECONCILED` — and it names the dimension |
| cannot be established | — | `INSUFFICIENT_EVIDENCE` |

No model participates in that classification. It is rules, it is unit-tested,
and it prints its reasoning.

---

## Setup and Run Instructions

```bash
git clone <this repo> && cd tieout
python -m pip install -r requirements.txt
```

There are two corpora, and the UI always says which one is on screen.

**Reproduce the four required cases with no API key:**

```bash
python run.py --gold
```

Builds a store from the eleven facts located by hand in the starter PDFs
(`tests/golden/cases.yaml`), pushed through the real grounding, normalization
and comparison code, and serves the viewer at <http://127.0.0.1:8000>. The UI
labels this corpus as the reference set.

**Replay a full extraction run with no API key:**

```bash
python run.py --demo
```

Replays the committed extraction cache over the starter PDFs — the same
result as a live run, zero API calls. This is the corpus the measured numbers
below come from.

**Live extraction:**

```bash
cp .env.example .env        # then put your Gemini key in it
python run.py               # ingests data/starter-datasets/, then serves
python run.py --paths some/other.pdf
```

**Tests and evaluation:**

```bash
python -m pytest -q                  # 80 tests, no API key needed
python -m tieout.eval --db data/gold.db
```

Python 3.10+. Only real dependencies are PyMuPDF, FastAPI and the Gemini SDK.

---

## Video Demo

*(link to be added)*

---

## Approach

### The pipeline

```
PDF
 └─ PyMuPDF: page text + word-level bounding boxes          deterministic
     └─ junk filter: pages that cannot assert anything      deterministic
         └─ Gemini Flash: structured extraction             model
             └─ GROUNDING GATE: is the quote really there?  deterministic
                 └─ normalize unit · magnitude · period · basis
                     └─ SQLite fact + evidence store
                         └─ blocking on (entity, metric)    deterministic
                             └─ 4-way frame comparator      deterministic
                                 └─ adjudication, only for unsettled aliases
                                     └─ FastAPI · evidence viewer
```

The model does one thing well — turning a sentence of financial English into
fields. Everything around it is deterministic, so a bad extraction surfaces as a
*rejected fact* rather than a wrong answer.

### The grounding gate

Every fact must carry a verbatim span. Before it is stored, the span is searched
for in the page text — exact, then whitespace/ligature-normalized, then fuzzy.
If it is not there, the fact is **rejected and counted**, with the model's raw
output kept in the `rejects` table.

This is what turns "cite your source" into "have one". It is also where the
hallucination rate in `/api/stats` comes from, and it is deliberately the one
step no model participates in.

It bites, too. The first version of the gold set quoted `"8,142 Cr"` for a
Delhivery figure; the gate refused it as too short to be evidence, which was
correct — the quote was widened to `"₹8,142 Cr FY24 revenue from services"`,
which carries the value, the period *and* the metric.

### Normalization is where the work is

Three publishers write the same year three ways, and one company writes a
different year the same way:

```
Economic Survey  "FY25"        ─┐
RBI              "2024-25"      ├─ 2024-04-01 … 2025-03-31
IMF              "FY2024/25"   ─┘
Delhivery        "FY24"        ─── 2023-04-01 … 2024-03-31
```

Tolerance comes from the precision each source claimed, not a fixed epsilon, so
`₹81,415.38 million` and `₹8,142 crore` agree while `6.4%` and `6.5%` do not —
even though the second pair is closer in absolute terms.

And `basis` carries the dimensions that turn an apparent contradiction into an
explained one: consolidation, **data vintage** (advance / provisional / revised
estimate / projection / realized), price basis, valuation, measure, geography.
Vintage is the highest-value field in the schema: institutional publishers
restate the same period repeatedly as data firms up, and a system that does not
model it reports every restatement as a contradiction.

### Where a model is and is not used

| step | engine |
|---|---|
| reading prose and tables into fields | Gemini Flash |
| verifying the quote exists | deterministic — the checker must not be the thing being checked |
| unit, magnitude, period, basis normalization | deterministic, unit-tested |
| candidate pair generation | deterministic blocking |
| relationship classification | deterministic, rule-traced |
| "do these two metric labels mean the same thing?" | Gemini Flash, capped at 40 calls, cached with its rationale |
| explanations | templated from the rule trace, so prose and verdict cannot drift |

### AI tools used

Built with Claude Code (Opus 5) — architecture, implementation and tests.
Extraction and alias adjudication at runtime use Google Gemini Flash. The four
demonstrated cases were located by reading the source PDFs by hand before any
extractor existed; they are not model output.

---

## The four required cases

Every one of these is in the extracted corpus — `python run.py --demo`, no API
key — and reproducible on the hand-labelled reference set with
`python run.py --gold`. Page numbers are 1-based indices into the excerpts.

### 1. Corroborated across documents, expressed differently

**RBI Annual Report 2024-25, p.22** — "6.5 per cent in **2024-25**"
**IMF Article IV 2025, p.10** — "India's real GDP grew by 6.5 percent in
**FY2024/25**"

Two institutions, two notations for one interval. → **CORROBORATES** (0.93).

A harder one, in the same corpus: the annual report's `₹81,415.38 million`
against the earnings deck's `₹8,142 crore` — and `₹1,266 Mn` against `₹127 Cr`
for EBITDA. Same figures, different scales, rounded differently. No string
matcher finds these; they match only after magnitude normalization at the
precision the *rounder* source claimed.

### 2. A genuine contradiction

**Economic Survey 2024-25, p.20** — "India's GDP at constant (2011-12) prices
grew by **6.7 per cent** and 5.4 per cent in **Q1** and Q2 **FY25**"
**RBI Annual Report 2024-25, p.24** — "real GDP rose (y-o-y) by **6.5 per
cent** in **Q1:2024-25**"

Same entity, metric, unit, price basis and measure. The two period notations
normalise to the same quarter. The values differ by 0.2 percentage points and
neither document explains why → **CONTRADICTS** (0.93).

This one only surfaces if quarters parse correctly. An earlier version read
`"Q1:2024-25"` as the whole of 2024-25 and reported a *different*, false
contradiction instead; see `docs/DECISIONS.md` D14.

### 3. An apparent contradiction explained by context

Found on four different dimensions:

- **Data vintage.** Economic Survey p.14 says **6.4%** for FY25; RBI p.8 says
  **6.5%** for 2024-25. Same country, metric and period — but the Survey quotes
  the first advance estimate. Different vintages of one measurement, not rival
  claims. → **reconciled (data vintage)**, 0.90.
- **Consolidation.** Delhivery's FY24 revenue is `₹74,540.82 million`
  standalone and `₹81,415.38 million` consolidated.
- **Measure.** GDP growth against GVA growth — different aggregates, not
  expected to match.
- **Sign convention.** A loss written `2,491.86` in the narrative and
  `(2,491.86)` in the statements is one figure, not two claims.

And a non-numeric one, from the reference set: Suvir Suren Sujan is a
Non-Executive Nominee Director in the 2022 prospectus (p.88) and *resigned with
effect from August 24, 2023* in the FY24 annual report (p.33) — a state change
over time, reconciled by the same machinery.

### 4. Extraction failures, measured

Three classes, all visible in the tooling rather than described:

- **Ungrounded claims — 72 of 1,967 (3.7%).** The model produced a quote that
  is not in the page. The gate rejects them and `/api/rejects` shows what it
  said.
- **First-person entities — 163.** Filings say "our Company" and "the Group".
  These are well-grounded facts that name nothing on their own; blocking on
  them would compare every filing's "company" facts against every other's, so
  they are refused. Resolving them to the document's subject is the first thing
  I would build next.
- **Tables lose their row headers.** All 22 remaining contradictions are
  same-page pairs like "Borrowings 1,316.09 vs 1,697.34, same date, same page"
  — current versus non-current, with the qualifier lost because reading order
  flattens a table into a stream. They are reported at 45% confidence *saying
  that*, rather than asserted. The fix is geometry-aware table reconstruction
  from the word boxes already extracted for the highlights.

A page with no text layer (IMF Article IV p.1, a scanned cover) yields nothing
at all and is counted among the 24 pages the junk filter skipped.

The negative control matters as much: Economic Survey p.4 says 6.4% and RBI
p.26 says 6.4%, same country, same period. A value-first system corroborates
them. They are GDP and GVA, and the comparator suppresses the pair
(`tests/test_compare.py`).

---

## Limitations and Next Steps

### Measured, on the corpus in `data/demo.db`

| | |
|---|---|
| documents / pages | 6 · 511 pages, 24 skipped by the junk filter |
| pages a model answered for | **284 of 487 candidates** — the run was cut short, see below |
| facts the model emitted | 1,967 |
| facts kept | 1,728 |
| facts refused by the gate | 239 (12.2%) — 163 unresolvable entity, 72 ungrounded, 4 unparseable |
| **hallucination rate** | **3.7%** — ungrounded claims / claims emitted |
| quote match quality | 1,714 exact, 12 fuzzy, 2 normalized (99.2% exact) |
| distinct metrics discovered | 962, none of them hard-coded |
| pairs compared | 2,325, against 1,492,128 if compared naively (0.16%) |
| relationships | 31 corroborates · 22 contradicts · 178 reconciled · 374 insufficient |
| suppressed | 387 same-document time series, 1,237 unresolved alias questions |
| relationship accuracy | 7/7 labelled pairs, 2/2 negative controls |

**The run is incomplete and the numbers say so.** The free-tier daily quota ran
out partway through, so 284 of 487 candidate pages were actually read. The
cache means resuming costs nothing, and every figure above is over the pages
that were read, not all of them. `pages_extracted` and `pages_candidate` are
reported separately in `/api/stats` for exactly this reason.

### What does not work yet

- **First-person entities are dropped, not resolved.** 163 well-grounded facts
  lost because the document said "our Company". This is the largest single
  source of lost recall and the first thing to fix.
- **Tables lose their row headers**, which is where every remaining
  contradiction comes from. Geometry-aware reconstruction using the word boxes
  already extracted for the highlights is the fix.
- **No OCR.** A scanned page yields nothing.
- **Relative periods are dropped.** 97 facts said "a year ago" or "the previous
  year" with no absolute anchor. They get no period, so any comparison
  involving them returns `INSUFFICIENT_EVIDENCE` — honest, but it is most of
  that bucket.
- **Entity resolution is deliberately shallow** — exact match, or one name
  contained in the other. It will not merge "Reserve Bank of India" with "RBI"
  unless a document writes them together. A wrong merge corrupts every
  comparison downstream, so this errs conservative.
- **No cross-currency comparison.** Reported incomparable rather than converted,
  because no rate is in evidence.
- **Confidence is composed, not calibrated.** A defensible product of grounding
  quality, frame completeness and period certainty — but not fitted against
  outcomes, because there are not enough labelled pairs to do that honestly.
- **The adjudicator was never exercised on the full corpus.** 1,237 alias
  questions went unanswered because the quota was gone; those pairs are
  suppressed rather than guessed. With budget, `revenue from operations` and
  `revenue from services` resolve and the pair corroborates — that path is
  tested (`tests/test_compare.py`).

### Next, in order

1. Resolve first-person entities against the document's subject.
2. Geometry-aware table reconstruction from the stored word boxes.
3. Calibrate confidence against a larger labelled set.
4. Let a reviewer correct a verdict in the UI, so corrections become new
   labelled pairs.

---

## Additional Notes

**Incremental ingest works and is not a special case.** Because blocks are keyed
on stored columns, adding a document extracts its facts and compares them only
against the members of the blocks they join — existing relationships are never
recomputed (`reason/blocking.py:pairs_for_new_facts`). Uploading a PDF through
the UI exercises this path. Re-uploading a document already in the layer is
detected by content hash and costs nothing.

**The decision log is `docs/DECISIONS.md`** — ten decisions with what else was
considered and what each one costs. The most useful entry is probably D4: a page
ranker was built, measured against the labelled set, found to rank the pages
carrying the headline claims in the bottom decile, and deleted.

**Reading the code.** The interesting file is `tieout/reason/compare.py` — about
150 lines, no dependencies beyond the normalizers, and it is the entire
classification logic. `tieout/normalize/` is where the real difficulty lives.

**Everything is inspectable without the UI:**

```bash
curl localhost:8000/api/stats
curl 'localhost:8000/api/relationships?label=CONTEXTUALLY_RECONCILED'
curl localhost:8000/api/facts/M1
sqlite3 data/gold.db 'select label, dimension, confidence from relationships'
```

No credentials are in this repository. `.env` is gitignored; `.env.example`
shows what is needed.
