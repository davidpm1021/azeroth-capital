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
