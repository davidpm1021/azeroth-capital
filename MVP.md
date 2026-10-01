# Azeroth Capital MVP

## Product question

Can a private tool collect enough trustworthy World of Warcraft Auction House history to surface unusual commodity-market conditions earlier and more clearly than manually watching current prices?

## MVP scope

The first MVP is **US region commodities only for analytics**. Connected-realm auction listings are retained, but we do not rank them yet because item variants make naive item-ID aggregation unsafe.

### Data acquisition

- [x] Blizzard OAuth client-credentials authentication
- [x] Region-aware official API client
- [x] Commodity auction snapshot collection
- [x] Connected-realm listing collection
- [x] Retry/backoff for network errors, rate limits, and transient server failures
- [x] Every poll retained as a time-series event
- [x] Identical raw payloads share one compressed blob
- [x] SQLite normalized storage
- [x] CSV export

### Snapshot analytics

- [x] Best listed price
- [x] Quantity at best
- [x] Total quantity
- [x] Depth within 1%, 5%, and 10% of best
- [x] Quantity-weighted listed price

### Temporal analytics

- [x] Best-price change
- [x] Total-quantity change
- [x] 5% depth change
- [x] Observed depletion per hour
- [x] 5% depth depletion ETA
- [x] Explainable market-pressure ranking
- [x] Explicit caveat that disappearance is not equivalent to confirmed sales

### Usability

- [x] `ac doctor`
- [x] `ac collect commodities`
- [x] `ac status`
- [x] `ac analyze`
- [x] On-demand item-name cache for top signals
- [x] Hourly Windows Task Scheduler installer
- [x] Local collector log
- [x] Windows setup guide
- [x] Automated tests in GitHub Actions

## MVP acceptance test

MVP is considered operational when a real machine can:

1. pass `ac doctor --live`;
2. collect two or more real commodity snapshots;
3. show increasing successful poll count while retaining unique raw blobs separately;
4. run `ac analyze` and produce a ranked report with item names;
5. run unattended for 24 hours through Windows Task Scheduler;
6. export the observed history to CSV.

## Deliberately post-MVP

Do not block real data collection on these:

- crafting recipe graphs
- profession profitability
- patch-note parsing
- weekly/reset seasonality
- rolling volatility and statistical baselines
- anomaly detection
- cross-realm non-commodity opportunity models
- realm population/demand modeling
- dashboard/web UI
- CraftLive integration
- notifications
- portfolio/accounting layer
- backtesting strategies
- machine learning

The next phase should be driven by what the first several days of real history reveal rather than assumptions.
