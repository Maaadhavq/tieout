# 3-minute demo script

Record at 1080p, `python run.py --demo` already running and warm. Show the
tool, never the terminal. Do not narrate setup.

Numbers marked `‹›` come from the live run — fill them from
`python -m tieout.eval --db tieout.db` before recording.

---

### 0:00 – 0:15 · The problem, stated with two real pages

*Screen:* two PDF pages side by side — RBI Annual Report p.8 and Economic
Survey p.4, each with its GDP sentence visible.

> "The Reserve Bank says India grew 6.5% in 2024-25. The Economic Survey says
> 6.4% for FY25. Same country, same measure, same year. One of these is not a
> contradiction — and a system that can't tell you which is useless for
> finance work."

### 0:15 – 0:35 · Ingest

*Screen:* the ledger, already populated. Point at the corpus line in the header.

> "Six documents, 511 pages — a prospectus, an annual report, an earnings deck,
> and three institutional reports. ‹N› facts extracted. ‹R› were thrown away,
> and I'll come back to why."

*Do not* film the ingest running. It takes minutes and shows nothing.

### 0:35 – 1:00 · Evidence is checked, not claimed

*Screen:* click any fact → click **page image** → the PDF page opens with the
sentence highlighted.

> "Every fact carries a quote. Before it's stored, the system goes back to the
> PDF and looks for that quote. If it isn't there — character for character —
> the fact is rejected and counted. That's the ‹R› from a moment ago. This
> highlight is drawn from the word geometry, so it's the actual sentence, not a
> page reference."

### 1:00 – 1:22 · Corroboration across wording and units

*Screen:* filter **Corroborates**. Select the RBI ≡ IMF GDP pair, then the
Delhivery revenue pair.

> "Two institutions, two notations — '2024-25' and 'FY2024/25' — one fact.
> And here's a harder one: the annual report says ₹81,415.38 million, the
> earnings deck says ₹8,142 crore. Same figure. String matching finds nothing;
> this matched after magnitude normalization, at the precision the rounder
> source claimed."

### 1:22 – 1:45 · A real contradiction

*Screen:* filter **Contradicts**. Show the rule trace with every check green
except `value`.

> "Same entity, metric, unit, period, and both are projections for 2025-26.
> Every comparability check passes. The values differ by 0.1 percentage points,
> beyond the precision either source stated, and nothing in either document
> explains the gap. That's a genuine disagreement between the RBI and the IMF."

### 1:45 – 2:15 · The case that matters

*Screen:* filter **Reconciled**. Open the vintage pair first. Let the trace sit
on screen for a beat — four checks green, `vintage` red.

> "Back to the opening. Same country, same metric, same period — the periods
> normalize to the same interval even though they're written differently. One
> dimension differs: the Survey is quoting the first advance estimate, the RBI
> the later figure. Different vintages of one measurement, not rival claims.
>
> The system names the dimension. That's the whole design: comparability is
> decided before agreement."

*Then click the director pair.*

> "And it isn't only numbers. Active in the 2022 prospectus, resigned in the
> FY24 report — a state change over time, reconciled the same way."

### 2:15 – 2:38 · What it gets wrong

*Screen:* the ledger footer, then `/api/rejects` or the rejects list.

> "‹R› facts — ‹P›% of what the model produced — failed grounding and were
> dropped. Two causes. One page in the IMF report is a scanned image with no
> text layer, so nothing can be grounded on it. And the earnings deck's tables
> come out column-major: row labels and their four period columns arrive as
> separate runs, so a value can't be bound to its period. The gate catches that
> class automatically, because a stitched-together quote isn't contiguous on the
> page. Fixing it means geometry-aware table reconstruction — using the word
> boxes I'm already extracting for the highlights. That's what I'd build next."

### 2:38 – 3:00 · An unseen document

*Screen:* drag in a PDF that is not in the starter set. Toast shows facts kept,
facts rejected, relationships added.

> "No document-specific rules anywhere — metric names are discovered at ingest,
> not enumerated. New document, and only the blocks its facts land in get
> recompared; nothing existing is rebuilt. That's the incremental path."

*Last frame:* the reconciled rule trace.

---

## Preparation checklist

- [ ] `python run.py --demo` warm, ledger loaded
- [ ] A 4th PDF ready to drag in — a public annual report, **not** from the starter set
- [ ] Numbers filled in from `python -m tieout.eval --db tieout.db`
- [ ] Pick the clearest instance of each label first; some pairs are noisier
- [ ] Browser at 1440×900, zoom 100%, no bookmarks bar
- [ ] Under 3:00. If tight, cut the second corroboration example, not the failure section.

## What not to do

- Do not show the terminal, install steps, or the code.
- Do not read the explanation paragraph aloud — let it sit on screen.
- Do not apologise for the failure section. It is the strongest 20 seconds.
