# Azeroth Capital

A standalone World of Warcraft Auction House market-data collector and quantitative research project.

## Goal

Build a trustworthy historical dataset from Blizzard's official APIs so we can study market depth, observed depletion, volatility, price breaks, crafting-chain relationships, and other auction-house inefficiencies.

Azeroth Capital is intentionally **research and decision support software**. It does not automate in-game purchases, sales, crafting, posting, or other player actions.

## MVP

The current MVP is designed to:

- collect regional commodity auction snapshots from Blizzard
- collect non-commodity snapshots for selected connected realms
- retain compressed raw source payloads for auditability
- normalize auction data into SQLite
- use Blizzard's `Last-Modified` timestamp as the market-observation time
- use conditional requests to avoid re-downloading unchanged auction snapshots
- calculate market depth at 1%, 5%, and 10% above best price
- compare distinct snapshots for price, quantity, and depth movement
- estimate observed depletion and a simple depth-exhaustion ETA
- rank markets with a transparent pressure heuristic
- cache item names and metadata
- export CSV history
- generate a local HTML market report
- run unattended on Windows through Task Scheduler

No Blizzard credentials are required to run the synthetic end-to-end demo:

```powershell
.\.venv\Scripts\ac.exe demo --open
```

Development happens on the `development` branch. Stable releases are merged to `main`.

## Prospective research

The Ubuntu collector freezes the top and bottom compression-gap quintiles at
each new snapshot. `ac paper-status`, `ac paper-candidates`, and
`ac paper-results --output data/paper-results.csv` inspect the experiment.
See [RESEARCH.md](RESEARCH.md) for evaluation rules and the v2 restart.
