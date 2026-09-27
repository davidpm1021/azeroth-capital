# Azeroth Capital MVP Setup

## 1. Requirements

- Windows 10/11
- Python 3.12 or newer
- Git
- A Blizzard Battle.net developer application with a client ID and client secret

Azeroth Capital uses Blizzard's OAuth client-credentials flow. The credentials identify this application to Blizzard. They are not your Battle.net login.

## 2. Clone and switch to development

```powershell
cd C:\Users\david
git clone https://github.com/davidpm1021/azeroth-capital.git
cd C:\Users\david\azeroth-capital
git switch development
git pull origin development
```

If the repo already exists, skip the clone line.

## 3. Create the virtual environment

Your PC currently has Python 3.14, so these commands avoid relying on `pip` being on PATH:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Activation is optional. Every command below can use the executable directly.

## 4. Configure credentials

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in:

```text
BLIZZARD_CLIENT_ID=your_client_id
BLIZZARD_CLIENT_SECRET=your_client_secret
WOW_REGION=us
WOW_LOCALE=en_US
AC_DATABASE_PATH=data/azeroth_capital.db
AC_RAW_DIR=data/raw
AC_TIMEOUT_SECONDS=60
```

Never commit `.env`.

## 5. Validate setup

```powershell
.\.venv\Scripts\ac.exe init
.\.venv\Scripts\ac.exe doctor
.\.venv\Scripts\ac.exe doctor --live
```

The live doctor check confirms that OAuth and Blizzard Game Data API access work.

## 6. Start collecting

```powershell
.\.venv\Scripts\ac.exe collect commodities
.\.venv\Scripts\ac.exe status
```

Wait for Blizzard to publish another snapshot, then collect again. Temporal analysis requires at least two successful polls.

## 7. Analyze market pressure

```powershell
.\.venv\Scripts\ac.exe analyze
```

The current MVP ranks commodities using an explainable pressure heuristic based on:

- best-price movement
- total listed-quantity movement
- depth within 5% of best price
- observed inventory depletion per hour
- estimated time to consume the currently visible 5% depth at the last observed depletion rate

This is an attention-ranking model, not a buy/sell recommendation. Auction disappearance can be caused by purchases, cancellations, expiration, or reposting.

A CSV copy is written to:

```text
data\latest_signals.csv
```

## 8. Install hourly unattended collection

First verify that manual collection works. Then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-hourly-task.ps1
```

This installs the Windows scheduled task:

```text
AzerothCapital-HourlyCollector
```

It starts about two minutes after installation and repeats hourly. Logs are written to:

```text
data\logs\collector.log
```

To remove it:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\remove-hourly-task.ps1
```

## 9. Connected-realm auctions

List Blizzard connected-realm IDs:

```powershell
.\.venv\Scripts\ac.exe realms
```

Collect one:

```powershell
.\.venv\Scripts\ac.exe collect realm --realm 60
```

Non-commodity realm data is retained in v0.1 but is not yet part of the market-pressure ranking because item variants can make naive item-ID aggregation misleading.

## 10. Export history

```powershell
.\.venv\Scripts\ac.exe export
.\.venv\Scripts\ac.exe export --item 123456 --output data\item_123456.csv
```

## 11. Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Data behavior

Each poll is retained as a time-series observation even if Blizzard returns the same auction payload as the previous poll. Identical raw payloads share a single compressed blob to avoid unnecessary disk growth.

Prices are stored as Blizzard integer currency values rather than floating point.
