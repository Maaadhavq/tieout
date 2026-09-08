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

---

## D16 — The verifier finds its cases by query, never by id
**2026-09-07**

`python -m tieout.verify` proves the four required cases. The tempting
implementation is to name the four relationship ids that look best on this
corpus.

**Why not:** that is a slideshow, not a verifier. It would pass on the starter
set and prove nothing about an unseen document — precisely the
document-specific logic the assignment rules out. Selection is instead
"highest-confidence instance of each label, preferring cross-document, and for
a reconciliation preferring a dimension a reader could not have spotted
themselves". `tests/test_verify.py` greps the source for row ids and document
names to keep it honest.

**Cost:** on a corpus where a label genuinely has no instance, the verifier
exits non-zero rather than showing something adjacent. That is the correct
answer, and it is why the exit code is worth having.

---

## D17 — Provenance columns are not optional in the CSV export
**2026-09-07**

Every exported fact row carries source document, page, the verbatim quote and
the match quality next to the value, and there is no flag to turn them off.

**Why:** the export is the moment a figure leaves this system and enters a
spreadsheet, where it will be copied, forwarded and eventually acted on. A
number that arrives without the sentence it came from has lost the only thing
that made it trustworthy. Making provenance a column rather than an option is
the whole argument of the project, applied to its own output.

**Cost:** wider files. Trivially worth it.

---

## D18 — The failure case belongs in the tool, not the terminal
**2026-09-07**

The fourth required case — an extraction failure and how it was handled — was
only reachable by `curl /api/rejects`. Everything else about the system was
inspectable in the UI; the one part that proves the system is honest was not.

The header now carries a fifth control, **Refused**, and it shows every claim
the gate would not accept: what the model said, the quote it offered, the page
it came from, and why the claim was thrown out. The evidence pane opens that
page so a reader can look for the quoted span and find it is not there.

**Why it matters more than it looks:** the hallucination rate in the footer is
the number an evaluator is most likely to disbelieve. Being able to click
straight from that number to the 239 claims it is made of turns it from a
statistic into something checkable in about ten seconds. `export refusals`
does the same for anyone who would rather audit it in a spreadsheet.

**Cost:** one more view to keep working, and a fifth pill in an already busy
header.

---

## D19 — Views are deep-linkable
**2026-09-07**

`#corroborates`, `#contradicts`, `#reconciled`, `#insufficient`, `#refused`.
Fifteen lines, and it means a specific case can be handed over as a link rather
than a set of instructions — useful in the README, and a safety net if a live
demo beat misbehaves on camera.

---

## D20 — A unit that is neither money nor a percentage keeps its own name
**2026-09-07**

Found by testing the normalizers on domains the starter set does not contain.
Everything that was not a currency or a percentage collapsed to `count`, so
`6.0 TWh` and `6000 GWh` compared as the same unit — and reported a
**contradiction**. The same held for millimetres against inches, tonnes against
kilograms, and any other pair a non-financial document would contain.

The unit is now the word the document wrote, reduced to one token. Spelling
variants of a single unit are folded (`mm` / `millimetre`, `tonne` / `ton`,
plus a conservative de-pluralisation), but nothing is ever converted — for
exactly the reason currencies never were: no factor is in evidence.

Measured on the corpus: the four reported labels did not move (30 / 22 / 174),
and `INSUFFICIENT_EVIDENCE` rose 350 → 379. Those 29 extra are pairs that were
being compared on a unit match that did not exist, including `INR vs share` and
`count vs equity`.

**Cost:** two documents using different spellings this map does not know will be
reported incomparable instead of compared. That is the safe direction, and the
map is three lines to extend.

---

## D21 — A possessive in front makes a phrase deictic whatever follows it
**2026-09-07**

`is_resolvable` refused a phrase only when it was *entirely* generic, so
"our Company" was caught but "the study", "our team" and "this trial" were not
— they name nothing on their own in exactly the same way, and they are what a
clinical or academic PDF is full of.

A leading possessive or demonstrative now disqualifies the phrase regardless of
its head noun, and the generic set gained the common deictic head-nouns. "The
Hague" still resolves, because "hague" is not generic.

**Cost:** an entity genuinely named "Our House" would be refused. Erring toward
refusal is the standing policy here, and the reason is in D11.

---

## D22 — Name containment is tested on word order, not on token sets
**2026-09-08**

`same_entity` folded one name into another when either token *set* contained
the other. Sets discard word order, and word order is exactly what separates
two institutions whose names share a head noun: "<Place> Bank of <Country>" and
"Bank of <Country>" are different banks, "<Place> National Bank" and "National
Bank" are different banks, and — the case that made the decision — a country
resolved as the same entity as its own central bank.

Containment is now a prefix test on the token sequence. English organisation
names put the distinguishing element first and append qualifiers, so dropping
trailing words preserves identity while dropping leading ones destroys it.

This was proved by calling `same_entity` directly, not by observing a bad
relationship: blocking keys on the exact `entity_key`, so no cross-entity pair
is ever generated and nothing in the pipeline could reach the branch. Auditing
recall is what turned it up — the 84 fact pairs blocking "misses" here are
overwhelmingly pairs that must not be compared, so the exact-key discipline is
load-bearing rather than merely conservative.

**Cost:** an abbreviation that drops leading words rather than trailing ones is
no longer folded, and a genuine parent/subsidiary pair that differs only by a
trailing word ("Acme" / "Acme Singapore") still folds even though they are
distinct legal entities. Both are unreachable today; if blocking is ever
widened to use this rule, the second is the one to fix first.

**Not done:** removing containment altogether. It is documented behaviour, and
the abbreviation case it exists for is real.

---

## D23 — A stated geography is kept, but only compared when both sides state it
**2026-09-08**

The extraction prompt asks for `geography` and the response schema declares it.
`normalize_basis` built its `known` map by walking `_VOCAB`, which has no entry
for geography because the dimension is open-ended and has nothing to enumerate;
the leftover pass skips anything already in `DIMENSIONS`. A stated geography
fell between the two and was discarded. The committed cache shows the model
returned one on **99 facts** and not one reached the store.

It is read now — `normalize_basis` walks `DIMENSIONS` and a dimension with no
rules reaches the existing verbatim fallback.

Reading it naively made things worse, which is the part worth recording. It
arrives on ~5% of facts and usually on one side of a pair, frequently just
restating the entity (34 of 96 were the entity's own name). Counted as a
dimension "one side states", it demoted the **required cross-document
contradiction** — Economic Survey p.20 6.7% against RBI p.24 6.5% — from
`CONTRADICTS` 0.93 to `INSUFFICIENT_EVIDENCE` 0.60, because the RBI page named
the country and the Survey page did not. Worse, `verify` still exited 0: it
finds cases by query, so it silently promoted the IMF p.3/p.13 value-binding
pair — the one relationship documented as confidently wrong — into the slot.

So geography is in `OPTIONAL`: stated by one side alone it is carried as
context and ignored for comparability, which is exactly the behaviour before
the dimension was read at all; stated by both, it is compared. Nothing
regresses and the gain is real.

**What it bought:** three ESG rows stop being contradictions. Lost Time Injury
Frequency Rate 0.56 against 1.21 and 0.75 against 2.46, and fatalities 7 against
19 — the model had correctly labelled these `Employees` and `Workers`, two
separate populations in one table, and the pipeline was throwing that label away
and calling them contradictions. Contradictions fall 22 → 19, reconciliations
rise 174 → 177. Time-series suppressions fall 387 → 384 because those same rows
across two periods now differ on two dimensions and are UNRELATED rather than
one series; both are suppressed, so the total is unchanged.

**Cost:** a genuine geography difference where only one document states it is
still missed — a state figure compared against a national one, with the state
named on one side only, still reads as a contradiction. Fixing that needs the
entity to carry its own scope, not a basis dimension.

**Not done:** treating the other five dimensions the same way. Vintage,
consolidation and price basis are stated systematically when they apply, and a
one-sided value there is real evidence of a gap — that asymmetry is what makes
case 3 work.

---

## D24 — The first screen of output is part of the submission
**2026-09-08**

Read cold, following the README literally from a fresh clone, two lines said
something the project did not mean.

`python run.py --demo` printed `ingest took 0.0s (0 model calls, 0 cache hits)`
immediately below a README promising that this command replays a committed
extraction cache with zero API calls. Both counters are zero on a fresh clone
because every document is already in the committed store and nothing needs
re-reading — but printed as two zeroes under that promise, it reads as though
the replay never happened and the figures beneath it are canned. It now says
what actually occurred.

`read 284 of 487 candidate pages — this run did not finish the corpus` named a
fact and no cause, so the most alarming line in the output read as a broken
submission rather than a documented limit. The cause is not knowable from
inside `run.py` — quota, an interrupt, or a missing key — so it names the
possibilities and says what the figures above it are computed over.

Both messages moved into `ingest_summary` and `coverage_note`, small pure
functions, so they can be asserted on rather than reviewed by eye.

`python -m tieout.verify` reproduces all four required cases in under a second
with no API key, and it sat ~70 lines into the README, below two corpora
explanations and the live-extraction path. It now leads the section. For a
reader with several hundred submissions to get through, the fastest proof in
the repository should not be the fifth thing they meet.

**Cost:** the README's opening is one section longer before a reader reaches
the viewer. That is the right trade: the viewer is what makes the case
memorable, but `verify` is what makes it credible, and credibility has to land
first.

**Not done:** anything else the cold read turned up was presentation, not
comprehension, and was left alone.

---

## D25 — A magnitude inside a rate's denominator does not scale the value
**2026-09-08**

`parse_quantity` searched the whole blob -- value, unit and magnitude hints
together -- for a scale word. The annual report states its safety metrics as
"Lost Time Injury Frequency Rate (LTIFR) (per one million-person hours worked)
Employees 0.56", and "million" inside that denominator was applied to the
value: four facts stored an injury frequency of 0.56 as **560,000**, and the
explanation rendered it as "0.56 million".

The model was right and we were wrong -- it returned `magnitude: null` for
every one of them. A magnitude word immediately preceded by "per" (optionally
"per one" / "per a") is now skipped, because it scales what the rate is
measured against rather than the quantity itself. A magnitude before "per"
still applies: "crore per annum" is a crore, annually.

**Cost:** "revenue per employee, in millions" loses its magnitude, because the
scale word trails the "per". That form is rarer than "per million X", and
dropping a magnitude understates a figure rather than inflating it by a
million, which is the safer direction to be wrong in.

Four values corrected. No label moved -- both sides of each comparison were
scaled identically, so the pairs agreed on a wrong number before and agree on
the right one now -- and no published figure changed.

---

## D26 — The junk filter's unit test is accidentally permissive, and stays that way
**2026-09-08**

`UNIT` in the prefilter matches `rs\.?` with no word boundary, so the "rs" in
"FACTORS", "quarters" and "figures" satisfies it. On 70 of 511 pages the unit
check passes only on a fragment of an ordinary English word, which makes the
"numbers present but no unit or period to anchor them" rejection nearly dead:
it fires on exactly one page in the starter set.

Measured before deciding. Anchoring the alternatives with `\b` would drop 8
more pages from the candidate set -- and those 8 pages hold **8 grounded facts**
that are in the corpus today. The module's own docstring says recall matters
more than precision here and the bar is deliberately low; tightening it would
trade 8 real facts for 8 model calls.

So it is left alone, and written down instead, because the next person to read
that regex will see the missing boundary and "fix" it. `pages_candidate` (487)
is therefore slightly generous, and the README's 284-of-487 is over that
generous denominator.

**Not done:** anchoring the regex; changing MIN_CHARS or MIN_NUMBERS. Neither
was measured to help.

---

## D27 — A near miss is containment, not a Jaccard floor
**2026-09-08**

Acceptance testing on documents the system had never seen found the failure the
assignment is actually about. One document wrote "revenue", another "revenue
from operations". Their token overlap is **0.333** against a `NEAR_MISS_MIN` of
**0.34**, so the pair was never proposed, the adjudicator never saw it, and both
a cross-magnitude corroboration (Rs. 10,000 million against Rs. 1,000 crore) and
a period reconciliation (FY2024 against FY2025) were lost to seven thousandths.

Lowering the floor was the obvious repair and the wrong one. Measured on the
starter corpus: 0.34 → 0.33 adds **714** pairs, and they are `gfce_growth`
against `real_growth`, `nominal_growth` against `real_growth`, `adjusted_ebitda`
against `ebitda_margin` — different metrics that share one common word. Those
would spend a capped 40-call adjudication budget on questions with an obvious
answer, which is the failure mode the build loop already warns about.

Containment is the sharper signal, and it is the same distinction that separates
two institutions sharing a head noun (D22). When one key's tokens are a subset
of the other's, one label is a more specific form of the other and the pair is a
real question. When they merely intersect, it usually is not.

**Cost:** +303 pairs rather than +714, all of the shape "income ~ other
comprehensive income" or "expenses ~ employee benefits expenses". Unresolved
alias questions rise 1,237 → 1,540 and pairs compared 2,325 → 2,628 (0.16% →
0.18% of naive). No relationship label moved: 30 corroborates, 19 contradicts,
177 reconciled, 379 insufficient, all unchanged, because every added pair is one
the rules refuse to settle alone.

**Still not fixed:** "headcount" against "number of employees" share no token at
all, so nothing proposes them and a genuine cross-document contradiction is
still missed. Catching that needs a synonym signal this system does not have.

---

## D28 — A currency the extractor dropped is read back from the quote
**2026-09-08**

"Rs. 1,000 crore" comes back as `value="1,000"`, `unit="crore"`: the scale word
lands in the unit field and the currency stays in the sentence. The figure then
normalized to a unitless `count`, so it would not compare against the same
amount written "Rs. 10,000 million" — exactly the cross-magnitude match this
system exists to make, failing on an unseen document while working on the
starter set, because there both sides happened to carry the currency.

The quoted sentence is now consulted for a currency, but only when the unit
field holds **nothing but a scale word**. That is the one case where it is safe:
the model has said the unit is a magnitude, so whatever currency the sentence
carries belongs to this number. A stated unit still wins, and a figure with no
unit at all is never given a currency from its sentence — otherwise a headcount
quoted beside a revenue figure would become rupees.

**Cost:** 20 facts in the starter corpus gained a currency they should always
have had. No published figure moved.

**Known and not fixed:** the unit a model returns for one sentence is not
stable. The same sentence, "The Company employed 1,200 people as at March 31,
2024", came back with `unit=null` in one document and `unit="people"` in
another, normalizing to `count` and `person`. The comparator then refuses the
pair as an unit mismatch, and two identical facts read as
`INSUFFICIENT_EVIDENCE` rather than corroborating. Making `count` compatible
with any unit would reopen D20 — TWh against GWh comparing as equal — so this
errs toward silence instead, and is recorded in the README's limitations.

---

## D29 — The adjudicator was calling a model that no longer exists
**2026-09-08**

The one place a model is asked to judge anything — "do these two metric labels
name the same quantity?" — defaulted to a hard-coded `gemini-2.5-flash`. That
alias returns **404 NOT_FOUND** on a key issued after it was retired, verified
directly against the key in `.env`:

    gemini-2.5-flash           FAIL-> ClientError: 404 NOT_FOUND
    gemini-flash-lite-latest   OK

`resolve()` wrapped the call in a bare `except` and returned `None`, which is
the same value it returns when it is deliberately offline or over budget. So
every adjudication failed, every failure was indistinguishable from a
legitimate "unresolved", `metric_aliases` stayed empty across the whole
project's life, and `/api/stats` reported "relationships decided by a model: 0"
— which reads like the rules settled everything rather than like a dead
dependency. The extractor already had a fallback chain; the adjudicator did not
share it.

It now walks the extractor's `MODEL_CHAIN`, drops a model that is unavailable
rather than treating a deployment fact as an answer about two labels, and keeps
the last error on the instance instead of discarding it.

**Proof it now runs.** Two unseen PDFs, live: `metric_aliases` gains real rows
with rationales, including the pair that motivated D27 —

    revenue ~ revenue_from_operations   same=False
      "Revenue from operations represents only the operating segment, whereas
       total revenue often includes ..."

Worth being straight about the outcome: the adjudicator judged those two labels
NOT the same, so the corroboration I expected still does not fire. That is a
defensible accounting answer, and the point is that the question is now asked
and the answer recorded, rather than the pair vanishing in silence.

**Cost:** none offline — with no key the adjudicator is still skipped and the
committed corpus is unchanged. With a key, up to 40 calls as documented.
