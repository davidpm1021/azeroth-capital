# Azeroth Capital v0.1 Setup

## 1. Requirements

- Windows 10/11
- Python 3.12+
- Git
- A Blizzard Battle.net developer application with a client ID and client secret

Create Blizzard developer credentials at the Battle.net Developer Portal. Azeroth Capital uses the OAuth client-credentials flow and sends the resulting bearer token only to Blizzard API endpoints.

## 2. Clone

```powershell
cd C:\Users\david
git clone https://github.com/davidpm1021/azeroth-capital.git
cd azeroth-capital
git switch development
```

## 3. Create a virtual environment

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

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

## 5. Initialize

```powershell
ac init
```

## 6. Test a commodity collection

```powershell
ac collect commodities
ac status
```

Commodity auction data is region-wide.

## 7. Inspect connected realms

```powershell
ac realms
```

Then collect a specific connected-realm auction house:

```powershell
ac collect realm --realm 60
```

Use a connected-realm ID returned by Blizzard. Do not assume a normal realm ID is interchangeable with a connected-realm ID.

## 8. Export observations

```powershell
ac export
ac export --item 123456 --output data\item_123456.csv
```

## 9. Run tests

```powershell
pytest
```

## Data model

Every successful collection keeps:

1. A compressed raw Blizzard response in `data/raw/`
2. A SHA-256 content hash for deduplication
3. Normalized SQLite rows in `data/azeroth_capital.db`
4. Derived commodity snapshot observations

Commodity observations currently include:

- best price
- quantity at best price
- total listed quantity
- depth within 1%, 5%, and 10% of best price
- quantity-weighted average listed price

Prices are stored in Blizzard's integer currency units rather than floating point.

## Current scope

v0.1 is deliberately a collector, not a trading bot or forecasting engine. It does not automate any in-game action.
