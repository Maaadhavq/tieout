# 3-minute demo script

Every landing state below was clicked and checked against a running server, not
remembered. Record at 1080p, browser at 1440x900, no bookmarks bar. Show the
tool, never the terminal.

What the assignment asks the video to contain is narrow: *"a demo video of 3
minutes or less showing a PDF being processed and the four required cases"*,
plus *"the source evidence and your system's reasoning for the first three"*.
Approach and Limitations are README sections. Do not narrate them here.

---

## Before you record

The first upload in a fresh server process takes **48.7 seconds**. `MODEL_CHAIN[0]`
is dead for this key and the extractor burns the full retry ladder before dropping
it. Once dropped it stays dropped for the life of the process, so the second
upload takes **7.7 seconds**. Warm it up off camera or the take dies.

```bash
cp data/demo.db tieout.db     # tieout.db is gitignored; the repo stays clean
python run.py                 # live extraction, all 6 documents already ingested
```

1. Open <http://127.0.0.1:8000>.
2. **Add PDF** with any throwaway PDF. Wait out the ~50 seconds.
3. Reset the layer *without restarting*, so the model chain stays warm:
   `python tieout-reset-demo.py` (kept outside the repo).
4. Refresh. The header must read **6 documents · 284/511 pages read · 1,726
   facts · 605 relationships** with a green **live extraction** chip.
5. Have the real demo PDF ready: **1 or 2 pages, never uploaded before.**

Do not rehearse with the PDF you will record with. A repeat upload of the same
file is a cache hit and returns in 0.4 s, so the take would show a replay rather
than a real extraction.

### Three things that will bite

- The **Refused** pill toggles. One click on, one click off. Turning it off
  empties the right pane to "Nothing selected". That is correct, not a crash.
- Collapse each **page image** before moving on. It goes full width.
- You never need the left ledger. The pills re-select for you.

---

## 0:00 - 0:20 · Open

Nothing to click. The page loads on the Delhivery pair: **A standalone
74,540.82 ₹ million**, **B consolidated 81,415.38 ₹ million**, **CONTEXTUALLY
RECONCILED**, *dimension: consolidation basis*, **0.90**.

> "Two numbers for the same company, the same metric, the same year, seven
> billion rupees apart. They are both right. One is the parent company's
> accounts, the other is the group's.
> Six documents, 511 pages, 1,726 facts. The system worked that out before it
> looked at either value."

Optional two seconds: click **Reconciled 177** so the pill lights up and shows
this is a category, not a one-off.

## 0:20 - 0:48 · The evidence is checked, not claimed

Click **`page image`** on card **A** in the *Source evidence* strip. Scroll down
a little. Click it again to collapse.

> "Every fact carries a quote. Before it is stored the system goes back to the
> PDF and looks for that quote character by character. If it is not there, the
> fact is thrown away.
> That is page 22 of the annual report, and that is the sentence, highlighted
> from the word geometry."

## 0:48 - 1:14 · Corroborates

Click **`Corroborates 30`**, then **scroll the right pane down 2 notches** so
both quote blocks are fully in frame.

Lands on: **A** India · real GDP growth · **2024-25** · **6.5%** (RBI p.22) ·
**B** · **FY2024/25** · **6.5%** (IMF p.3) · **CORROBORATES 0.93**.

> "RBI writes 6.5 per cent in 2024-25. The IMF writes 6.5 percent in FY2024/25.
> Two institutions, two notations, one fact."

Point at the trace header, which reads `7 dimensions · 0 model calls`:

> "Seven checks, zero model calls. The model reads the page. It never decides
> the verdict."

## 1:14 - 1:44 · A real contradiction

Click **`Contradicts 19`**, then **scroll down 2 notches** again.

Lands on: **A** Economic Survey p.20 · **Q1 FY25** · **6.7%** · **B** RBI p.24 ·
**Q1:2024-25** · **6.5%**. Six green ticks, red ✗ on `value`:
*6.7 vs 6.5 · Δ 0.2 · tolerance ±0.05*. **CONTRADICTS 0.93**.

Let the trace hold for a beat. It is the point.

> "Both notations normalise to the same quarter. Entity, metric, unit, price
> basis and measure all match. Only the value differs, by 0.2 points, and
> neither document explains why."

## 1:44 - 2:14 · What it gets wrong

Click **`Refused 241`** once.

Lands on: `NSE Nifty 50 · End-Period Index · 2024-25 → 23,519.4`, reason **quote
not found**, offered quote `"NSE Nifty 50: End-Period ... 23,519.4"`, searched in
the RBI annual report **p.96**.

Click **`page image`**. The card reads *"Nothing to highlight, the quote above is
not on this page."*

> "241 claims were refused. 72 of them, 3.7% of everything the model produced,
> quoted something that is not on the page.
> Look at those three dots. That is the model welding a row label onto a number
> from another column. It reads like a phrase; it is never contiguous in the
> document.
> Here is page 96. Nothing is highlighted because there is nothing to highlight."

Do not apologise here. Collapse the page image, then click **Refused** again to
clear it.

## 2:14 - 2:48 · A document it has never seen

Click **`Add PDF`** and pick the unseen PDF. About 8 seconds.

> "A document the system has never read. New facts, grounded the same way,
> compared against everything already in the layer. 961 metric names discovered,
> none of them written into the code."

Read the toast when it lands. It says, verbatim:

> `5 facts kept, 0 refused (0% ungrounded), 20 new relationships,`
> **`nothing existing recomputed.`**

> "Nothing existing recomputed. A new document joins the blocks it belongs to.
> It does not rebuild the layer."

## 2:48 - 3:00 · Close

Click **`Reconciled 177`** to end on a verdict with its trace on screen.

> "Comparability first, agreement second. Everything here is one command from a
> clean clone, and the failures are in it on purpose."

---

## If a beat breaks mid-take

`python -m tieout.verify` prints all four cases with evidence in one screen in
0.1 s with no API key. That is the recovery shot.

## Afterwards

`rm tieout.db`, then paste the video URL into the one line under `## Video Demo`
in the README.
