# Tools

Scripts and utilities for the TradingAgent project.

---

## Scripts

### `testagent-azure.py`

Production agent runner using **Azure OpenAI** (GPT-5.4). Runs multi-ticker
analysis in parallel and saves markdown reports.

```
python tools/testagent-azure.py [watchlist.json] [--filter PATTERN]
```

| Arg | Default | Description |
|---|---|---|
| `watchlist` | `watchlist.json` | Watchlist JSON file in `tools/` |
| `--filter`, `-f` | *(none)* | Regex or substring to match category names |

**Examples:**
```bash
python tools/testagent-azure.py watchlist.json
python tools/testagent-azure.py earnings_watchlist.json --filter "large-cap"
python tools/testagent-azure.py earnings_watchlist.json -f "nasdaq.*2026-04-28.*mega"
python tools/testagent-azure.py watchlist-india.json
```

Reports saved to: `reports/{trade_date}-{watchlist_name}/`

---

### `testagent-copilot-github.py`

Agent runner using **GitHub Copilot** models via an OpenAI-compatible API
gateway at `http://127.0.0.1:3030/v1`.

```
python tools/testagent-copilot-github.py
```

Hardcoded: `NVDA AAPL MSFT GOOGL TSLA` on `2026-04-25`.

---

### `testagent-ollama.py`

Quick local test using **Ollama** (`qwen3:32b` / `deepseek-r1:32b`).

```
python tools/testagent-ollama.py
```

Hardcoded: `NVDA` on `2026-04-27`. Requires `ollama serve` running on port 11434.

---

### `upcoming_earnings_calendar.py`

Fetches earnings calendars from **NASDAQ** and/or **NSE**, enriches with
market-cap tiers, and writes a categorized watchlist JSON.

```
python tools/upcoming_earnings_calendar.py [--source {nasdaq,nse,both}] [--days N] [--out PATH]
```

| Arg | Default | Description |
|---|---|---|
| `--source` | `both` | Data source: `nasdaq`, `nse`, or `both` |
| `--days` | `14` | Number of calendar days to look ahead |
| `--out` | `earnings_watchlist_{today}.json` | Output file path |

**Examples:**
```bash
python tools/upcoming_earnings_calendar.py --source nasdaq --days 7
python tools/upcoming_earnings_calendar.py --source nse --days 14
python tools/upcoming_earnings_calendar.py --source both --days 14 --out tools/earnings_watchlist.json
```

---

### `upcoming_earnings_calendar_all.py`

Multi-exchange forward earnings calendar covering **NYSE, AMEX, ARCA,
NQ.NM, NQ.SC, BATS, NSE, BSE**. Pulls from the NASDAQ calendar API for
US names (then filters per-ticker to the requested venue via yfinance),
the NSE event-calendar API, and the BSE forthcoming-results API. Output is
a watchlist JSON keyed by `{exchange}_{date}_{cap-tier}`.

```
python tools/upcoming_earnings_calendar_all.py [--region {US,INDIA,ALL}] [--exchanges LIST] [--days N] [--out PATH] [--no-mcap]
```

| Arg | Default | Description |
|---|---|---|
| `--region` | `ALL` | Convenience group: `US` (NYSE/AMEX/ARCA/NQ.NM/NQ.SC/BATS), `INDIA` (NSE/BSE), or `ALL` |
| `--exchanges` | *(none)* | Comma-separated subset of `NYSE,AMEX,ARCA,NQ.NM,NQ.SC,BATS,NSE,BSE`. Overrides `--region` |
| `--days` | `14` | Calendar days to look ahead |
| `--out` | `earnings_watchlist_{region}_{today}.json` | Output file path |
| `--no-mcap` | *(off)* | Skip per-ticker market-cap lookup (faster; cap tier becomes `unknown-cap`) |

**Examples:**
```bash
# US only, next 7 days
python tools/upcoming_earnings_calendar_all.py --region US --days 7

# India only (NSE + BSE), next 30 days
python tools/upcoming_earnings_calendar_all.py --region INDIA --days 30

# All 8 exchanges, default 14-day window
python tools/upcoming_earnings_calendar_all.py

# Custom subset (overrides --region)
python tools/upcoming_earnings_calendar_all.py --exchanges NYSE,NQ.NM,NQ.SC --days 14
```

---

### `upcoming_earnings.py`

Scans tickers from a watchlist and prints those with **earnings within N days**
(uses yfinance).

```
python tools/upcoming_earnings.py [watchlist.json] [days]
```

| Arg | Default | Description |
|---|---|---|
| `watchlist` | `watchlist.json` (in `tools/`) | Watchlist JSON file |
| `days` | `21` | Days to look ahead |

---

### `test.py`

Performance diagnostic for yfinance data fetching (MACD indicators, 30-day
lookback on AAPL).

```
python tools/test.py
```

---

### `watchlist_manager.py`

**Module** — not run directly. Provides the `Watchlist` class used by the other
scripts.

```python
from watchlist_manager import Watchlist

# Load existing
wl = Watchlist.load("tools/watchlist.json")
wl.tickers()                                  # all unique tickers
wl.tickers(filter="large-cap")                # tickers in matching categories
wl.filter_categories("nasdaq.*mega")          # {cat: "SYM SYM ..."} dict

# Create new
wl = Watchlist("output.json", trade_date="2026-04-28", max_workers=5)
wl.add("my_picks", "AAPL MSFT GOOGL")
wl.add("my_picks", ["NVDA", "AMZN"])
wl.remove("my_picks", "MSFT")
wl.save()
wl.print_summary()

# Properties
len(wl)                   # total unique tickers
"AAPL" in wl              # membership check
wl.categories             # {name: "SYM SYM ..."}
wl.category_names()       # sorted list of names
wl.to_dict()              # raw dict
```

---

## Watchlist JSON Schema

All watchlist files (`watchlist.json`, `watchlist-india.json`,
`earnings_watchlist.json`) follow the same structure:

```json
{
    "trade_date": "<string>",
    "max_workers": "<int>",
    "categories": {
        "<category_name>": "<space-separated ticker symbols>"
    }
}
```

### Fields

| Field | Type | Description |
|---|---|---|
| `trade_date` | `string` | Date or date range for analysis. ISO format (`2026-04-28`) or range (`2026-04-27_to_2026-05-11`) |
| `max_workers` | `int` | Max parallel threads for `testagent-azure.py` |
| `categories` | `object` | Map of category name → space-separated ticker symbols |

### Category Naming Conventions

**Manual watchlists** (`watchlist.json`, `watchlist-india.json`):
```
mega_cap_tech          ← free-form descriptive name
semiconductors
nuclear_energy
```

**Generated earnings watchlists** (`earnings_watchlist.json`):
```
{exchange}_{date}_{cap-tier}
```

| Part | Values |
|---|---|
| `exchange` | `nasdaq`, `nse`, `bse`, `nyse`, `amex`, `arca`, `nqnm`, `nqsc`, `bats` |
| `date` | ISO date (`2026-04-28`) |
| `cap-tier` | `mega-cap`, `large-cap`, `mid-cap`, `small-cap`, `micro-cap`, `unknown-cap` |

**Examples:**
```
nasdaq_2026-04-28_large-cap
nyse_2026-05-29_mega-cap
nqsc_2026-05-30_small-cap
nse_2026-04-29_mid-cap
bse_2026-04-30_micro-cap
```

### Ticker Symbol Format

| Exchange | Format | Example |
|---|---|---|
| NASDAQ / NYSE / AMEX / ARCA / BATS | Plain symbol | `AAPL`, `GOOGL`, `BRK.A` |
| NSE / India | Symbol + `.NS` suffix | `RELIANCE.NS`, `TCS.NS`, `M&M.NS` |
| BSE / India | Symbol + `.BO` suffix | `RELIANCE.BO`, `TCS.BO` |
