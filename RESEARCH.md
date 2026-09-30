# Research protocol

The signal remains depth compression minus positive price extension, using five
observations, the Midnight catalog, the existing liquidity filters, and the top
and bottom 20%. In-game decisions and execution remain manual.

## Evaluation correction on 2026-09-30

Blizzard supplies HTTP dates such as `Sun, 27 Sep 2026 ...`. Alphabetical sorting
placed Monday before Sunday, contaminating historical windows and some frozen
selections. Storage now orders parsed UTC instants while retaining the original
source timestamps and all existing data. This also fixes paper-status endpoints,
candidate display, and CSV ordering across weekdays and months.

Research, backtests, and paper results now use the first observation **at or after**
the requested holding period, no more than 1.5 hours late. Earlier observations
cannot be labeled matured. Actual entry and exit timestamps are in CSV exports;
hourly publication jitter may turn a nominal three-hour horizon into about four
hours. Results should not be compared with the older nearest-observation rule
without recomputing them.

New selections use `compression-gap-v2-h5-q0.20` and
`compression-gap-bottom-v2-h5-q0.20`. Legacy selections are preserved as recorded,
evaluated with the corrected exit rule, and displayed separately. Do not rewrite
old selections or backfill controls and call them prospective. The v2 identifier
marks an implementation correction, not a tuned signal formula.

## Reading results

- Candidate averages and medians describe reference-price moves after a modeled
  5% successful-sale cut. They are not realized trading returns.
- Cohort statistics first average within a snapshot, then give each snapshot
  equal weight. Adjacent snapshots and repeated items remain dependent; neither
  the candidate count nor the cohort count is an independent-trial count.
- Controls match both experiment identity and observation time. Top-only legacy
  cohorts do not enter the top-minus-bottom comparison. The control table shows
  its own matched top return, bottom return, and matched cohort count.
- Missing valid exits are excluded. Matched cohorts require an evaluated member
  on both sides but do not guarantee complete exit coverage of every candidate.
- The current experiment does not model inventory, available capital, entry
  latency, slippage, deposits, fill probability, or failed sales. The reference
  quote predates collection and cannot establish an executable entry price.

## Next evidence gate

Collect a fresh v2 record across at least a full weekly cycle before deciding
whether to extend it. Inspect matched cohort spreads, candidate and cohort
medians, repeat-item dependence, missing exits, and sensitivity to extreme gains.
One week is an initial review point, not proof of an edge. Keep formula and
filters fixed; any changed strategy needs another experiment identity.

The next execution-oriented milestone is a conservative size/entry-latency and
sale-fill model with a manual trade journal. A positive gross research spread
alone does not justify deploying game gold.

## Economic premise comparison — fixed September 30, 2026

Use real-market mechanisms as hypotheses, not as evidence that WoW must offer
a profitable opportunity. Cont, Kukanov and Stoikov study price impact from
two-sided order flow and depth in stocks ([paper](https://arxiv.org/abs/1011.6402)).
Our hourly sell listings lack their transaction and bid-side data: disappearing
inventory is not confirmed buying, and our depth measure is not order-flow
imbalance. Novy-Marx shows the overfitting risk of selecting combinations of many
signals ([paper](https://www.nber.org/papers/w21329)); this comparison fixes a
small set before prospective evaluation rather than optimizing combinations.

`ac premise-scan` now runs in the hourly collector. It preserves v2 top/bottom
selections and adds three separately named benchmarks, from exactly the same
Midnight universe, five-observation history and entry liquidity filters:

| Premise | Frozen rule | Question |
|---|---|---|
| Compression v2 | Existing gap top 20% | Does the current combined signal help? |
| Price discount v1 | Lowest price change versus baseline, top 20% | Does simple cheapness outperform? |
| Depth only v1 | Largest near-price depth reduction, top 20% | Does scarcity alone outperform? |
| Eligible market v1 | All eligible items, equal weight | Is performance just general market movement? |

The benchmark ranking uses item ID to resolve ties. The existing v2 scanner is
unchanged. Benchmark rows record original entry prices and selection times;
past cohorts are never backfilled in prospective storage. These are rankings,
so the highest-ranked discount/depth items need not have a positive discount
or compression in every snapshot. Overlap between arms is expected.

Primary endpoint: **6-hour modeled net return**, equal weight per cohort, with
paired excess versus market and discount. Three and twelve hours are descriptive
sensitivity checks; do not pick a new horizon merely because it wins this sample.
Keep both candidate and cohort medians visible. A head-to-head win is a practical
strategy comparison, not proof of an isolated causal contribution from depth.

`ac premise-results --output data/premise-results.csv` evaluates two scenarios:

1. `source_quote`: the recorded selection quote, an optimistic price reference.
2. `next_snapshot`: the first strictly later source quote at/after all four arms
   were frozen, within 1.5h of that availability time. Start the holding period
   from this new entry; do not charge the earlier entry price or keep its exit.

The comparison uses only cohorts with all expected frozen members and valid
entries/exits for **every arm in both scenarios**. Counts of seen, fully frozen,
and fully evaluated cohorts expose omissions. Missing outcomes never rerank
the entry universe or silently remove individual losers. Completeness filtering
can still bias the sample; counts are descriptive, not independent trials.

Next-snapshot entry is a latency sensitivity probe, not an executable fill:
source quotes themselves reach us later, quantities are not modeled, and there
is no sale-volume/fill evidence. Net includes only the modeled 5% sale cut.

`ac premise-research --output data/premise-research.csv` applies the same fixed
rules to past observations, selecting before inspecting future returns. It uses
actual collection completion as the earliest information-availability time
(collection start if completion is absent), and never writes paper rows. It
requires at least ten eligible items per source time and holds the current
Midnight catalog fixed. Treat its results as exploratory, not a holdout test.

Decision gate: if compression fails to beat simple cheapness prospectively,
drop the added complexity rather than tune it until it wins. If a candidate
survives delay, costs and outlier checks, test conservative sizing and sell-fill
assumptions next. Recipe input/output cost relationships and recurring demand
cycles are later hypotheses requiring additional data and longer history.
