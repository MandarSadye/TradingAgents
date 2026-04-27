"""Fetch the full earnings calendar for the next N days from NASDAQ and/or NSE.

Outputs a watchlist.json-compatible file with earnings categorized by exchange,
market-cap tier, and earnings time.

Usage:
    python upcoming_earnings_calendar.py [--source nasdaq|nse|both] [--days 14] [--out earnings_watchlist.json]

Examples:
    python upcoming_earnings_calendar.py --source nasdaq --days 7
    python upcoming_earnings_calendar.py --source nse --days 14
    python upcoming_earnings_calendar.py --source both --days 14 --out earnings_watchlist.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests
import yfinance as yf

from watchlist_manager import Watchlist

NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings?date={d}"
NSE_INDEX_URL = "https://www.nseindia.com"
NSE_CAL_URL = "https://www.nseindia.com/api/event-calendar"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_nasdaq(d: date) -> list[dict]:
    """Return earnings rows for a single trading date from NASDAQ."""
    url = NASDAQ_URL.format(d=d.isoformat())
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        print(f"  ! NASDAQ {d}: {e}", file=sys.stderr)
        return []
    rows = (payload.get("data") or {}).get("rows") or []
    out = []
    for row in rows:
        out.append({
            "date": d.isoformat(),
            "symbol": row.get("symbol"),
            "name": row.get("name"),
            "time": row.get("time"),                    # "time-pre-market" / "time-after-hours" / "time-not-supplied"
            "eps_estimate": row.get("epsForecast"),
            "market_cap": row.get("marketCap"),
            "fiscal_quarter": row.get("fiscalQuarterEnding"),
            "source": "nasdaq",
        })
    return out


def fetch_nse(start: date, end: date) -> list[dict]:
    """Return Board Meeting / Financial Results events from NSE."""
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    try:
        # warm up to get cookies
        s.get(NSE_INDEX_URL, timeout=15)
        params = {
            "index": "equities",
            "from_date": start.strftime("%d-%m-%Y"),
            "to_date": end.strftime("%d-%m-%Y"),
        }
        r = s.get(NSE_CAL_URL, params=params, timeout=20)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        print(f"  ! NSE {start}..{end}: {e}", file=sys.stderr)
        return []
    rows = payload if isinstance(payload, list) else payload.get("data", [])
    out = []
    for row in rows:
        purpose = (row.get("purpose") or "").lower()
        # filter to results-related events; NSE returns dividends, splits, etc. too
        if "result" not in purpose and "earning" not in purpose:
            continue
        out.append({
            "date": row.get("date") or row.get("bm_date"),
            "symbol": (row.get("symbol") or "") + ".NS",
            "name": row.get("company") or row.get("sm_name"),
            "purpose": row.get("purpose"),
            "details": row.get("bm_desc") or row.get("details"),
            "source": "nse",
        })
    return out


def _parse_market_cap(raw: str | None) -> float | None:
    """Parse NASDAQ market-cap strings like '$1,234,567,890' or '$2.5B' to float."""
    if not raw:
        return None
    s = raw.replace("$", "").replace(",", "").strip()
    if not s:
        return None
    multiplier = 1.0
    if s[-1].upper() == "T":
        multiplier, s = 1e12, s[:-1]
    elif s[-1].upper() == "B":
        multiplier, s = 1e9, s[:-1]
    elif s[-1].upper() == "M":
        multiplier, s = 1e6, s[:-1]
    elif s[-1].upper() == "K":
        multiplier, s = 1e3, s[:-1]
    try:
        return float(s) * multiplier
    except ValueError:
        return None


def _cap_tier(mcap: float | None) -> str:
    """Classify a market-cap value into a human-friendly tier."""
    if mcap is None:
        return "unknown-cap"
    if mcap >= 200e9:
        return "mega-cap"
    if mcap >= 10e9:
        return "large-cap"
    if mcap >= 2e9:
        return "mid-cap"
    if mcap >= 300e6:
        return "small-cap"
    return "micro-cap"


def _fetch_mcaps_yfinance(symbols: list[str]) -> dict[str, float | None]:
    """Batch-fetch market caps via yfinance for symbols missing that field (e.g. NSE)."""
    if not symbols:
        return {}
    print(f"  Fetching market caps for {len(symbols)} NSE symbols via yfinance...")
    mcaps: dict[str, float | None] = {}
    for sym in symbols:
        try:
            info = yf.Ticker(sym).fast_info
            mcaps[sym] = getattr(info, "market_cap", None)
        except Exception:
            mcaps[sym] = None
    return mcaps


# ---------------------------------------------------------------------------
# Enrichment: attach resolved market cap + tier to each raw earnings record
# ---------------------------------------------------------------------------

def enrich_results(results: list[dict]) -> list[dict]:
    """Return a copy of results with 'mcap_value' and 'cap_tier' added to each record."""
    nse_syms = list({r["symbol"] for r in results
                     if r.get("source") == "nse" and r.get("symbol")})
    nse_mcaps = _fetch_mcaps_yfinance(nse_syms)

    enriched = []
    for r in results:
        r = dict(r)  # shallow copy
        src = r.get("source", "unknown")
        if src == "nasdaq":
            r["mcap_value"] = _parse_market_cap(r.get("market_cap"))
        else:
            r["mcap_value"] = nse_mcaps.get(r.get("symbol"))
        r["cap_tier"] = _cap_tier(r["mcap_value"])
        enriched.append(r)
    return enriched


# ---------------------------------------------------------------------------
# Output formatting: build a Watchlist from enriched records.
# Edit *only this section* to change category naming conventions.
# ---------------------------------------------------------------------------

def category_key(record: dict) -> str:
    """Derive the category key for one enriched record.

    Current format:  <exchange>_<date>_<tier>
    e.g. nasdaq_2026-04-28_large-cap, nse_2026-04-29_mid-cap
    """
    src = record.get("source", "other")
    d = str(record.get("date", "unknown"))
    tier = record.get("cap_tier", "unknown-cap")
    return f"{src}_{d}_{tier}"


def build_watchlist(enriched: list[dict], trade_date_label: str) -> Watchlist:
    """Assemble a Watchlist instance from enriched records."""
    wl = Watchlist(trade_date=trade_date_label)
    for r in enriched:
        sym = r.get("symbol")
        if not sym:
            continue
        wl.add(category_key(r), sym)
    return wl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["nasdaq", "nse", "both"], default="both")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--out", type=Path, help="Optional JSON output path (watchlist format)")
    args = ap.parse_args()

    today = date.today()
    end = today + timedelta(days=args.days)
    print(f"Fetching earnings calendar from {today} to {end} (source={args.source})\n")

    results: list[dict] = []

    if args.source in ("nasdaq", "both"):
        for i in range(args.days + 1):
            d = today + timedelta(days=i)
            if d.weekday() >= 5:  # skip weekends
                continue
            rows = fetch_nasdaq(d)
            print(f"  NASDAQ {d}: {len(rows)} tickers")
            results.extend(rows)
            time.sleep(0.4)  # be polite

    if args.source in ("nse", "both"):
        rows = fetch_nse(today, end)
        print(f"  NSE {today}..{end}: {len(rows)} events")
        results.extend(rows)

    print(f"\nTotal: {len(results)} events")

    if not results:
        print("No results — nothing to write.")
        return 0

    # --- Enrich → Format → Write ---
    enriched = enrich_results(results)
    label = f"{today.isoformat()}_to_{end.isoformat()}"
    wl = build_watchlist(enriched, trade_date_label=label)
    fp = args.out or Path(f"earnings_watchlist_{today}.json")
    wl.save(fp)
    print(f"\nWrote {fp}")
    wl.print_summary()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
