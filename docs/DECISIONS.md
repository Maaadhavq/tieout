# Decision log

Each entry: what was decided, what else was considered, why, and what it costs.
Dates are the day the decision was taken.

---

## D1 — Find the cases in the PDFs before writing any extractor
**2026-09-07**

The first two hours went into reading the six starter PDFs by hand and locating
concrete instances of all four required cases, with page numbers and verbatim
quotes, recorded in `tests/golden/cases.yaml`.

**Considered:** build extraction first, then look for interesting pairs in the
output.

**Why:** the failure mode of this assignment is arriving at hour fifteen with a
working extractor and no clean contradiction to show. Locating the cases first
turns the demo into a fixed target and gives the comparator a labelled test set
that does not depend on extraction quality.

**Cost:** two hours before a line of pipeline code existed. It paid for itself
immediately — three of the eight labelled pairs were wrong on first reading, and
finding that out against a hand-built fixture was much cheaper than finding it
out against model output.

---

## D2 — The grounding gate: a fact without a locatable quote is not stored
**2026-09-07**

Every extracted fact must carry a verbatim span. Before the fact is written,
`ingest/ground.py` searches the page text for that span — exact, then
whitespace/ligature-normalized, then fuzzy at 0.90. A span that cannot be found
means the fact is rejected and written to `rejects` with what the model emitted.

**Considered:** trust the model's own confidence score; ask a second model to
verify; store everything and mark provenance quality.

**Why:** a model's confidence in a quote is not evidence that the quote exists.
String search against the source is cheap, deterministic, and cannot be talked
out of its answer. It also produces a number worth publishing — the share of
model output that could not be traced to a page — which is the honest version of
"how much does it hallucinate".

**Cost:** recall. A correct fact whose quote the model paraphrased is thrown
away. Given the assignment explicitly prefers grounded facts over more facts,
that is the right side to err on. The minimum quote length of 12 characters also
means a bare number is never accepted as evidence, which cost one gold fact
(D3) until its quote was widened to include the metric and period.

---

## D3 — Predicates are discovered, not enumerated
**2026-09-07**

There is no list of known metrics anywhere in the codebase. `metric_key` slugs
whatever label the document used and interns it in the `metrics` table.
Normalization is linguistic only — acronym folding, stopword removal,
orthographic collapses.

**Considered:** a fixed taxonomy of financial metrics; an embedding index over
metric labels.

**Why:** the assignment says the solution must not rely on hard-coded facts,
filenames, schemas or document-specific rules, and that it will be tested with
new PDFs. A taxonomy would work beautifully on the starter set and fail on the
first unseen document type. A discovered slug works on rainfall and headcount as
readily as on revenue.

**Cost:** two labels for the same quantity produce two keys. That is what the
adjudicator is for (D6), and unresolved cases are reported as
`INSUFFICIENT_EVIDENCE` rather than guessed.

---

## D4 — The page prefilter ranks nothing; measurement killed the ranker
**2026-09-07**

`ingest/prefilter.py` began as a cost control: score every page by numeric
density and spend a fixed model budget on the densest ones. Scored against the
hand-labelled set, the ranking turned out to be inverted.

| page | quote | density rank |
|---|---|---|
| RBI Annual Report p8 | "growth moderated to 6.5 per cent in 2024-25" | 93 / 100 |
| Economic Survey p4 | "estimated to grow by 6.4 per cent in FY25" | 87 / 89 |

A narrative page states one figure in a paragraph; a statistical appendix states
four hundred. Numeric density measures table-ness, not importance, and a budget
spent on the densest pages would have dropped the headline claims. The ranker was
removed. What remains is a junk filter for pages structurally incapable of
asserting anything — no text layer, almost no text, numbers with no unit or
period nearby. On this corpus it skips 24 of 511 pages.

**Considered:** keep the ranker with a much larger budget; rank by a
model-scored relevance pass.

**Why:** a heuristic that is confidently wrong is worse than none. And on
measurement, cost was not the binding constraint anyway: at Flash-tier pricing
the whole corpus is well under a dollar, so wall-clock and rate limits matter
more than token spend, and those are addressed with concurrency and the cache.

**Cost:** the "large PDFs" extension is answered by the cache and concurrency
rather than by skipping most of the document. Honest, but less impressive to
quote.

---

## D5 — Comparability is decided before agreement
**2026-09-07**

`reason/compare.py` walks five claim-frame dimensions — entity, metric, unit,
period, basis — and only then looks at values. The label falls out of what the
walk found. A pair whose frame differs on two or more dimensions is suppressed
entirely rather than reported as reconciled.

**Considered:** compare values first and explain differences afterwards; hand
each pair to a model and ask for the relationship.

**Why:** value-first is how a system decides that GDP growth of 6.4% and GVA
growth of 6.4% corroborate each other. They are not the same claim. Frame-first
makes "are these even comparable" an explicit, inspectable step, and makes
`CONTEXTUALLY_RECONCILED` mean something precise: exactly one named dimension
differs. Handing pairs to a model is O(n²) calls, non-deterministic, and cannot
show its working.

**Cost:** the frame has to be right, which pushes all the difficulty into
normalization. Facts whose period or basis the document never stated come back
`INSUFFICIENT_EVIDENCE` instead of a verdict.

---

## D6 — The model answers exactly one question
**2026-09-07**

The only judgement delegated to a model at reasoning time is: do two metric
labels name the same measured quantity? Answers are cached in `metric_aliases`
with a rationale, capped at 40 calls per run, and a hedged answer (confidence
< 0.6) is discarded rather than recorded.

**Considered:** let the model adjudicate whole relationships; let it resolve
entity identity too.

**Why:** an alias is a stable, reusable fact about vocabulary — one call settles
a whole block. A relationship verdict is a per-pair judgement that would have to
be re-explained every time and could not be unit-tested. Entity resolution was
left deterministic because a wrong entity merge silently corrupts every
downstream comparison, and the conservative rule (exact match or one name
contained in the other) is auditable.

**Cost:** genuine aliases the rules miss and the cap does not reach stay
unresolved. The system says so rather than guessing.

---

## D7 — Tolerance comes from the precision each source claimed
**2026-09-07**

`normalize/units.py` derives tolerance from how a number was written: half of
the last stated decimal place, scaled by magnitude. Two values agree if they are
within the looser of their two tolerances.

**Considered:** a fixed relative epsilon (0.5%, 1%).

**Why:** a fixed epsilon gets both of these wrong. `₹8,142 crore` and
`₹81,415.38 million` are the same figure reported by two departments at
different precision, and must agree. `6.4%` and `6.5%` differ by far less in
absolute terms and must not. Precision-aware tolerance handles both without a
special case.

**Cost:** it trusts the source's formatting. A figure written with spurious
precision gets held to it.

---

## D8 — Splitting `price_basis` from `valuation`
**2026-09-07**

The basis vocabulary originally filed constant/current prices and basic/market
prices under one dimension. It was split when the comparator refused to
reconcile a real pair: "GDP at constant prices" against "GVA at basic prices"
registered as two differing dimensions and was suppressed, when it is one
conceptual difference (GDP vs GVA) plus a valuation convention.

**Why:** inflation adjustment and tax/subsidy treatment are orthogonal — a
figure can be GVA at basic prices at constant prices. Conflating them makes a
single difference look like two and hides the reconciliation.

**Cost:** one more dimension to populate. Where only one document states it, the
comparator reports it `unknown` and lowers confidence rather than treating it as
a difference — which is why that pair now reconciles at 0.70 rather than 0.90.

---

## D9 — SQLite, no vector store, no graph database
**2026-09-07**

**Considered:** Neo4j for the relationship graph; a vector store for metric and
entity matching; LangChain or LlamaIndex for the pipeline.

**Why:** the assignment states that a graph database or visualization alone is
not the solution, and that a smaller understandable prototype beats a larger
system whose behaviour is unclear. The actual difficulty here is normalization
and frame comparison, and neither is helped by nodes and edges or by nearest
neighbours. Blocking on `(entity_key, metric_key)` is one indexed query.
Everything the system knows fits in six tables that can be read with `sqlite3`.

**Cost:** near-miss metric matching is token overlap rather than embedding
similarity, so it is blunter. It escalates to the adjudicator instead.

---

## D10 — A reference corpus that runs with no API key
**2026-09-07**

`python run.py --gold` builds a store from the eleven hand-labelled facts,
pushed through the real grounding, normalization and comparison code, and the UI
labels it as the reference set. `python run.py --demo` replays the committed
extraction cache.

**Why:** the assignment asks that a project depending on a paid service still be
evaluable without the author's account. Two modes cover two different questions:
`--gold` proves the reasoning on facts a human verified, `--demo` proves the
whole pipeline including extraction. The evaluation harness refuses to report a
hallucination rate for the reference set, where it would be trivially zero.

**Cost:** two extra code paths, and the need to be explicit everywhere about
which corpus is on screen.

---

## D11 — First-person entities are refused, not resolved
**2026-09-07**

Measured on 1,670 extracted facts: 163 of them (9.8%) had an entity of
"Company", "Group", "Our Company", "the Company" or similar. Filings are
written in the first person and the extractor reproduces that, despite the
prompt telling it not to.

**Considered:** resolve them to the document's subject; leave them and let
them sort themselves out.

**Why refuse:** blocking is keyed on the entity. Left alone, every filing's
"company" facts land in one block and get compared against every other
filing's — which is precisely how a system starts reporting that two unrelated
companies contradict each other. This is worse than a missing fact, because it
is a confident wrong answer.

Resolving them properly needs a document-level subject, which the extractor
does not currently carry. It is the right fix and is listed in Limitations.

**Cost:** ~10% of extracted facts are dropped, including real ones. The
rejection reason `unresolvable_entity` is counted alongside the others, so the
loss is visible rather than silent.

---

## D12 — One lock around the SQLite connection
**2026-09-07**

The first full run died partway through the fourth document with
`cannot commit - no transaction is active`. Extraction runs pages on a thread
pool and every worker writes to the cache through one shared connection
(`check_same_thread=False`), so two threads interleaved inside a transaction.

**Considered:** a connection per thread; a write queue.

**Why:** a single `RLock` is four lines and this is not a write-throughput
problem — the work is waiting on a remote model, not on SQLite. Verified with
600 concurrent writes across 16 threads.

**Cost:** writes serialise. Irrelevant at this scale; would matter if
extraction ever became local and fast.

---

## D13 — A model that keeps failing is dropped for the run
**2026-09-07**

The extractor walks a chain of models and falls through on failure. Measured
on the live run, 61 of the first 78 pages were served by the *last* model in
the chain: the two preferred ones were returning 503 constantly, and every
page was paying the full retry ladder on each of them first — about 21 seconds
of sleeping per page, which was most of the wall clock.

A model that exhausts its retries is now skipped for the rest of the run (the
last resort is never skipped). Throughput went from 5.4 to about 30
pages/minute.

**Cost:** a model that was only briefly down stays out for the whole run. A
time-based cooldown would be kinder; a run is short enough that it does not
matter.

**Related, and worth stating plainly:** the free-tier daily quota ran out
partway through the corpus. 250 of 487 candidate pages were extracted. That is
a budget limit, not a system limit — the cache means resuming costs nothing —
but every number reported from this run is over those 250 pages, not all 487.

---

## D14 — Parse a quarter by splitting it off, not by one pattern
**2026-09-07**

Three false verdicts on the live corpus all came from trying to spell every
year form into the quarter regex:

| written | was read as | should be |
|---|---|---|
| `Q2 of FY25` | the whole of FY25 | Jul–Sep 2024 |
| `Q1:2024-25` | the whole of 2024-25 | Apr–Jun 2024 |
| `first quarter of FY2025/26` | Apr–Jun 2024 | Apr–Jun 2025 |

The first two produced false contradictions — a quarterly figure compared
against an annual one as though the periods matched. The third landed twelve
months early, because in a span form the first year is the year the fiscal year
*starts*, while the quarter path took it as the year it ends.

`parse_period` now pulls the quarter marker off the front and re-parses the
remainder with the ordinary year rules, so a quarter inherits whatever
convention its year form carries. One rule instead of four, and adding a new
year form automatically works for quarters too.

**What it bought:** the false contradictions disappeared, and a real one
surfaced that needed both notations parsed correctly — Economic Survey p.20
"6.7 per cent … in Q1 … FY25" against RBI p.24 "6.5 per cent in Q1:2024-25".

**Cost:** two passes over the string instead of one. Irrelevant.

---

## D15 — Report what was read, not what was queued
**2026-09-07**

`pages_scanned` counted pages that passed the junk filter, which is what the
system *intended* to read. When the free-tier quota ran out mid-corpus, that
number still said 487 while only 284 pages had actually been answered for — so
every rate derived from it was quietly computed over a corpus larger than the
one that existed.

`/api/stats` now reports `pages_candidate` and `pages_extracted` separately,
the UI shows "284/511 pages read", and the evaluation harness prints a line
saying the run was cut short. A metric that flatters itself when a run fails
is worse than no metric.
