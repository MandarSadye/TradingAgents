"""Print tickers from a watchlist that report earnings within the next N days.

Usage:
    python scripts/upcoming_earnings.py [watchlist.json] [days]

Defaults: watchlist.json, 21 days.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf


def load_tickers(path: Path) -> list[str]:
    data = json.loads(path.read_text())
    tickers: list[str] = []
    for syms in data.get("categories", {}).values():
        tickers.extend(syms.split())
    # de-dup, preserve order
    seen = set()
    return [t for t in tickers if not (t in seen or seen.add(t))]


def next_earnings(ticker: str) -> date | None:
    try:
        t = yf.Ticker(ticker)
        df = t.get_earnings_dates(limit=8)
        if df is None or df.empty:
            return None
        today = datetime.now(df.index.tz) if df.index.tz else datetime.now()
        future = df[df.index >= today]
        if future.empty:
            return None
        return future.index.min().date()
    except Exception as e:
        print(f"  ! {ticker}: {e}", file=sys.stderr)
        return None


def main() -> int:
    wl = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "watchlist.json"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 21

    tickers = load_tickers(wl)
    cutoff = date.today() + timedelta(days=days)
    print(f"Scanning {len(tickers)} tickers for earnings on/before {cutoff}...\n")

    hits: list[tuple[date, str]] = []
    for sym in tickers:
        d = next_earnings(sym)
        if d and d <= cutoff:
            hits.append((d, sym))

    hits.sort()
    print(f"\n{len(hits)} ticker(s) reporting in next {days} days:\n")
    for d, sym in hits:
        print(f"  {d.isoformat()}  {sym}")
    print()
    print("Space-separated:", " ".join(s for _, s in hits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
