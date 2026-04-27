"""Fetch the full earnings calendar for the next N days from NASDAQ and/or NSE.

Usage:
    python scripts/earnings_calendar.py [--source nasdaq|nse|both] [--days 14] [--out file.json]

Examples:
    python scripts/earnings_calendar.py --source nasdaq --days 7
    python scripts/earnings_calendar.py --source nse --days 14
    python scripts/earnings_calendar.py --source both --days 14 --out earnings.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["nasdaq", "nse", "both"], default="both")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--out", type=Path, help="Optional JSON output path")
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

    # group by date for display
    by_date: dict[str, list[dict]] = {}
    for r in results:
        by_date.setdefault(str(r.get("date")), []).append(r)
    for d in sorted(by_date):
        rows = by_date[d]
        syms = " ".join(sorted({r["symbol"] for r in rows if r.get("symbol")}))
        print(f"\n{d}  ({len(rows)})")
        print(f"  {syms}")

    if args.out:
        args.out.write_text(json.dumps(results, indent=2, default=str))
        print(f"\nWrote {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
