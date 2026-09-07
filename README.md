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

Replays the committed extraction cache over all six starter PDFs — same result
as a live run, zero API calls.

**Live extraction:**

```bash
cp .env.example .env        # then put your Gemini key in it
python run.py               # ingests data/starter-datasets/, then serves
python run.py --paths some/other.pdf
```

**Tests and evaluation:**

```bash
python -m pytest -q                  # 69 tests, no API key needed
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

All four are reproducible with `python run.py --gold`. Page numbers are 1-based
indices into the starter excerpts.

### 1. Corroborated across documents, expressed differently

**RBI Annual Report 2024-25, p.8** — "growth moderated to **6.5 per cent** in
**2024-25**"
**IMF Article IV 2025, p.10** — "India's real GDP grew by **6.5 percent** in
**FY2024/25**"

Two institutions, two period notations, two phrasings. Same normalized frame,
same value → **CORROBORATES** (0.93).

A second one crosses magnitude units: the annual report's
`₹81,415.38 million` and the earnings deck's `₹8,142 crore` are the same figure,
matched only after magnitude normalization and precision-aware tolerance.

### 2. A genuine contradiction

**RBI Annual Report, p.17** — "real GDP growth for **2025-26** is projected at
**6.5 per cent**"
**IMF Article IV, p.13** — "real GDP growth is projected at **6.6 percent** in
**FY2025/26**"

Same entity, metric, unit, period, and both are projections. Every frame check
passes; the values differ by 0.1pp, beyond the precision either source stated.
Nothing in either document explains the gap → **CONTRADICTS** (0.93).

### 3. An apparent contradiction explained by context

Three of these, on three different dimensions:

- **Data vintage.** RBI p.8 says 6.5% for 2024-25; Economic Survey p.4 says
  **6.4%** for FY25. Same country, same metric, same period — but the Survey
  quotes the *first advance estimate* published in January 2025 and the RBI
  reports the realized figure. Different vintages of one measurement, not rival
  claims. → **CONTEXTUALLY_RECONCILED (data vintage)**, 0.90.
- **Consolidation.** Delhivery's FY24 revenue is `₹74,540.82 million` standalone
  and `₹81,415.38 million` consolidated, both on annual-report p.22.
  → **reconciled (consolidation basis)**, 0.90.
- **State change over time**, and non-numeric: Suvir Suren Sujan is a
  Non-Executive Nominee Director in the 2022 prospectus (p.88) and *resigned
  with effect from August 24, 2023* in the FY24 annual report (p.33).
  → **reconciled (reporting period)**, 0.90.

### 4. An extraction failure, and what it costs

Two, both real and both visible in the tooling:

- **A page with no text layer.** IMF Article IV p.1 is a scanned cover: zero
  characters, one image. No fact can be grounded on it, so none is emitted. The
  junk filter reports it as skipped rather than silently ignoring it. Fixing it
  means an OCR path, which this prototype does not have.
- **Column-major table streams.** The Delhivery earnings deck emits table row
  labels and their four period columns as separate text runs — the reading-order
  stream on p.8 is `Pin-code reach 18,074 18,540 18,675 18,793` with the four
  period headers elsewhere on the page. A flat text extractor cannot bind a
  value to its period. The grounding gate catches this class automatically,
  because a model that stitches such a value together produces a quote that is
  not contiguous on the page — so the fact is rejected rather than stored wrong.
  The fix is geometry-aware table reconstruction using the word boxes already
  being extracted for provenance; it is the first thing I would build next.

The negative control matters as much: Economic Survey p.4 says **6.4%** and RBI
p.26 says **6.4%**, same country, same period. A value-first system corroborates
them. They are GDP and GVA — different aggregates — and the comparator suppresses
the pair. `tests/test_compare.py` asserts this.

---

## Limitations and Next Steps

**Measured, on the reference set:** relationship accuracy 7/7 on labelled pairs,
2/2 negative controls, every stored highlight verified to cover its quote
(`tests/test_evidence_boxes.py` re-reads each rectangle out of the PDF).

**What does not work yet:**

- **No OCR.** A scanned page yields nothing. One page of the starter set is
  affected.
- **Tables lose their structure.** Described above. This is the single largest
  source of missed facts, and the reason the extractor is instructed to prefer
  claims stated in sentences.
- **Recall is untuned.** The gate errs towards rejecting, and the extractor is
  capped at 12 claims per page. The system is built to be right about what it
  reports, not to report everything.
- **Entity resolution is deliberately shallow** — exact match or one name
  contained in the other. It will not merge "Reserve Bank of India" with "RBI"
  unless a document writes them together, and it will not notice that two
  differently written addresses are one place. A wrong merge corrupts every
  comparison downstream, so this errs conservative.
- **No cross-currency comparison.** A rupee figure and a dollar figure are
  reported incomparable rather than converted, because no rate is in evidence.
- **Confidence is composed, not calibrated.** It is a defensible product of
  grounding quality, frame completeness and period certainty, but it has not
  been fitted against outcomes — there are not enough labelled pairs to do that
  honestly.
- **The adjudicator is capped at 40 calls** per run. Past that, unresolved
  aliases stay `INSUFFICIENT_EVIDENCE`.

**Next, in order:** geometry-aware table reconstruction from the word boxes;
calibrating confidence against a larger labelled set; an OCR fallback; and
letting a reviewer correct a verdict in the UI so corrections become new
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
