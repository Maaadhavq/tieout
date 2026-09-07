# 3-minute demo script

Record at 1080p, `python run.py --demo` already running and warm. Show the
tool, never the terminal. Do not narrate setup.

Numbers below are from the committed corpus (`python -m tieout.eval --db
data/demo.db`). Re-check them if you re-run extraction.

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
> and three institutional reports. 1,728 facts extracted. 239 were thrown away,
> and I'll come back to why."

*Do not* film the ingest running. It takes minutes and shows nothing.

### 0:35 – 1:00 · Evidence is checked, not claimed

*Screen:* click any fact → click **page image** → the PDF page opens with the
sentence highlighted.

> "Every fact carries a quote. Before it's stored, the system goes back to the
> PDF and looks for that quote. If it isn't there — character for character —
> the fact is rejected and counted. This highlight is drawn from the word
> geometry, so it's the actual sentence in the actual document — not a page
> reference you'd have to go and check yourself."

### 1:00 – 1:22 · Corroboration across wording and units

*Screen:* filter **Corroborates**. Select the RBI ≡ IMF GDP pair, then the
Delhivery revenue pair.

> "The RBI writes '6.5 per cent in 2024-25'. The IMF writes '6.5 percent in
> FY2024/25'. Two institutions, two notations, one fact.
> And a harder one: the annual report says ₹1,266 million for EBITDA, the
> earnings deck says ₹127 crore. Same figure. String matching finds nothing —
> this matched after magnitude normalization, at the precision the rounder
> source claimed."

### 1:22 – 1:45 · A real contradiction

*Screen:* filter **Contradicts**. Show the rule trace with every check green
except `value`.

> "The Economic Survey says GDP grew 6.7 per cent in Q1 FY25. The RBI says 6.5
> per cent in Q1:2024-25. Two different notations that normalise to the same
> quarter — same entity, metric, unit, price basis, measure. Every comparability
> check passes. The values differ by 0.2 percentage points and neither document
> explains why. That's a genuine disagreement between two Indian institutions,
> and it only shows up if the quarters parse correctly."

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

> "239 facts were refused. 72 of them — 3.7% of everything the model produced —
> were ungrounded: it gave a quote that isn't in the page, and the gate threw
> them out. Another 163 were perfectly well grounded but said 'our Company',
> which names nothing on its own.
>
> And every remaining contradiction is a table artifact: borrowings 1,316
> against 1,697, same date, same page — current versus non-current, with the row
> header lost because reading order flattens a table into a stream. Those are
> reported at 45% confidence saying exactly that, rather than asserted. The fix
> is geometry-aware table reconstruction, using the word boxes I'm already
> extracting for these highlights."

### 2:38 – 3:00 · An unseen document

*Screen:* drag in a PDF that is not in the starter set. Toast shows facts kept,
facts rejected, relationships added.

> "This is a Delhivery investor presentation filed with the exchange in August
> 2025 — the system has never seen it. 51 facts, 50 new relationships, and it
> connects: active customers were 33,278 in the Q4 FY24 deck and 35,277 here.
> 962 metric names, none of them written into the code. Only the blocks its
> facts land in get recompared; nothing existing is rebuilt."

*Last frame:* the reconciled rule trace.

---

## Preparation checklist

- [ ] `python run.py --demo` warm, ledger loaded
- [ ] `data/unseen/delhivery-investor-presentation-2025-08-01.pdf` ready to drag in
      (BSE filing, not in the starter set; needs a GEMINI_API_KEY to extract live)
- [ ] Numbers re-checked with `python -m tieout.eval --db data/demo.db`
- [ ] `python -m tieout.verify` exits 0 (all four cases present in the corpus)
- [ ] Pick the clearest instance of each label first; some pairs are noisier
- [ ] Browser at 1440×900, zoom 100%, no bookmarks bar
- [ ] Under 3:00. If tight, cut the second corroboration example, not the failure section.

## If a live beat fails while recording

`python -m tieout.verify` prints all four cases with evidence, traces and page
numbers in one screen. It is a legitimate fallback for the 1:00–2:15 stretch and
takes about ten seconds — better than fighting the UI on camera. Do not use it
for the whole demo: the evidence viewer is the thing worth showing.

## What not to do

- Do not show the terminal, install steps, or the code.
- Do not read the explanation paragraph aloud — let it sit on screen.
- Do not apologise for the failure section. It is the strongest 20 seconds.
