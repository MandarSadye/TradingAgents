"""Fetch the full forward earnings calendar across all supported exchanges.

Supported exchanges:
  * US:     NYSE, AMEX (NYSE American), ARCA (NYSE Arca),
            NQ.NM (NASDAQ Global / Global-Select),
            NQ.SC (NASDAQ Capital Market),
            BATS (Cboe BZX)
  * India:  NSE, BSE

Sources:
  * NASDAQ Earnings Calendar API  -> all US-listed names (filtered per-ticker
    to the requested exchanges via yfinance).
  * NSE  India Event Calendar API -> board meetings flagged "Results".
  * BSE  India CorpForthcoming API -> forthcoming results.

Output is a watchlist.json-compatible file categorised by exchange / date /
market-cap tier (e.g. ``nyse_2026-05-29_large-cap``).

Usage:
    python upcoming_earnings_calendar_all.py [options]

Options:
    --region     Convenience group: US | INDIA | ALL  (default: ALL)
                 US     = NYSE, AMEX, ARCA, NQ.NM, NQ.SC, BATS
                 INDIA  = NSE, BSE
                 ALL    = every supported exchange
    --exchanges  Comma-separated subset of:
                 NYSE, AMEX, ARCA, NQ.NM, NQ.SC, BATS, NSE, BSE
                 Overrides --region when provided.
    --days       Look-ahead window in calendar days (default: 14)
    --out        Output JSON path (default: earnings_watchlist_<region>_<today>.json)
    --no-mcap    Skip per-ticker market-cap lookup (fast, but tier = unknown-cap)

Examples:
    python upcoming_earnings_calendar_all.py --region US --days 7
    python upcoming_earnings_calendar_all.py --region INDIA --days 30
    python upcoming_earnings_calendar_all.py --days 14                       # all exchanges
    python upcoming_earnings_calendar_all.py --exchanges NYSE,NQ.NM,NQ.SC    # custom subset
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import yfinance as yf

from watchlist_manager import Watchlist

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALL_EXCHANGES = ["NYSE", "AMEX", "ARCA", "NQ.NM", "NQ.SC", "BATS", "NSE", "BSE"]
US_EXCHANGES  = {"NYSE", "AMEX", "ARCA", "NQ.NM", "NQ.SC", "BATS"}
IN_EXCHANGES  = {"NSE", "BSE"}

REGIONS = {
    "US":    US_EXCHANGES,
    "INDIA": IN_EXCHANGES,
    "ALL":   set(ALL_EXCHANGES),
}

NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings?date={d}"

NSE_INDEX_URL = "https://www.nseindia.com"
NSE_CAL_URL   = "https://www.nseindia.com/api/event-calendar"

BSE_INDEX_URL = "https://www.bseindia.com/corporates/Forth_Results.aspx"
BSE_CAL_URL   = "https://api.bseindia.com/BseIndiaAPI/api/CorpForthCommingActivity/w"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Map yfinance ``exchange`` codes → our canonical exchange labels.
# yfinance returns codes such as: NYQ, ASE, PCX, NMS, NCM, NGM, BTS, BATS, NSI, BSE.
YF_EXCHANGE_MAP = {
    "NYQ":  "NYSE",
    "NYS":  "NYSE",
    "ASE":  "AMEX",
    "AMX":  "AMEX",
    "PCX":  "ARCA",
    "ARCA": "ARCA",
    "NMS":  "NQ.NM",   # NASDAQ Global Select / Global Market
    "NGM":  "NQ.NM",
    "NCM":  "NQ.SC",   # NASDAQ Capital Market
    "BTS":  "BATS",
    "BATS": "BATS",
    "NSI":  "NSE",
    "BSE":  "BSE",
}

# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------

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
            "time": row.get("time"),
            "eps_estimate": row.get("epsForecast"),
            "market_cap": row.get("marketCap"),
            "fiscal_quarter": row.get("fiscalQuarterEnding"),
            "source": "us",
        })
    return out


def fetch_nse(start: date, end: date) -> list[dict]:
    """Return Financial-Results board-meeting events from NSE."""
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    try:
        s.get(NSE_INDEX_URL, timeout=15)  # warm cookies
        params = {
            "index": "equities",
            "from_date": start.strftime("%d-%m-%Y"),
            "to_date":   end.strftime("%d-%m-%Y"),
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
        if "result" not in purpose and "earning" not in purpose:
            continue
        raw_date = row.get("date") or row.get("bm_date") or ""
        out.append({
            "date": _norm_in_date(raw_date),
            "symbol": (row.get("symbol") or "") + ".NS",
            "name": row.get("company") or row.get("sm_name"),
            "purpose": row.get("purpose"),
            "details": row.get("bm_desc") or row.get("details"),
            "source": "nse",
            "exchange": "NSE",
        })
    return out


def fetch_bse(start: date, end: date) -> list[dict]:
    """Return forthcoming Results events from BSE India."""
    s = requests.Session()
    s.headers.update({**BROWSER_HEADERS, "Referer": BSE_INDEX_URL})
    try:
        s.get(BSE_INDEX_URL, timeout=15)
        params = {
            "Purposecode": "P",          # Results
            "strPrevDate": start.strftime("%Y%m%d"),
            "strToDate":   end.strftime("%Y%m%d"),
            "ddlcategorys": "R",
            "ddlindustrys": "",
            "scripcode": "",
        }
        r = s.get(BSE_CAL_URL, params=params, timeout=20)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        print(f"  ! BSE {start}..{end}: {e}", file=sys.stderr)
        return []
    rows = payload if isinstance(payload, list) else payload.get("Table", [])
    out = []
    for row in rows:
        purpose = (row.get("Purpose") or row.get("PURPOSE") or "").lower()
        if "result" not in purpose and "earning" not in purpose:
            continue
        scrip_id   = (row.get("scrip_Id")   or row.get("ScripId")   or "").strip()
        scrip_code = (row.get("scrip_Code") or row.get("ScripCd")   or "").strip()
        # Prefer ".BO" suffix on the alphanumeric ID for yfinance compatibility.
        sym = (scrip_id + ".BO") if scrip_id else (scrip_code + ".BO")
        raw_date = row.get("Meeting_Date") or row.get("MeetingDate") or row.get("DT_TM") or ""
        out.append({
            "date": _norm_in_date(raw_date),
            "symbol": sym,
            "name": row.get("company_Name") or row.get("CompanyName"),
            "purpose": row.get("Purpose") or row.get("PURPOSE"),
            "details": row.get("Remarks")  or row.get("REMARKS"),
            "source": "bse",
            "exchange": "BSE",
        })
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm_in_date(raw: str) -> str:
    """Normalise Indian-source date strings to ISO ``YYYY-MM-DD``."""
    if not raw:
        return ""
    raw = str(raw).strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d",
                "%d/%m/%Y", "%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:len(fmt) + 8].strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return raw  # give up; keep raw string


def _parse_market_cap(raw: str | None) -> float | None:
    if not raw:
        return None
    s = raw.replace("$", "").replace(",", "").strip()
    if not s:
        return None
    mult = 1.0
    if s[-1].upper() == "T": mult, s = 1e12, s[:-1]
    elif s[-1].upper() == "B": mult, s = 1e9,  s[:-1]
    elif s[-1].upper() == "M": mult, s = 1e6,  s[:-1]
    elif s[-1].upper() == "K": mult, s = 1e3,  s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def _cap_tier(mcap: float | None) -> str:
    if mcap is None:        return "unknown-cap"
    if mcap >= 200e9:       return "mega-cap"
    if mcap >= 10e9:        return "large-cap"
    if mcap >= 2e9:         return "mid-cap"
    if mcap >= 300e6:       return "small-cap"
    return "micro-cap"


def _yf_lookup(sym: str) -> tuple[str | None, float | None]:
    """Return (canonical_exchange, market_cap) for a ticker via yfinance."""
    try:
        t = yf.Ticker(sym)
        fi = t.fast_info
        mcap = getattr(fi, "market_cap", None)
        exch = getattr(fi, "exchange", None) or getattr(fi, "exchange_name", None)
        canon = YF_EXCHANGE_MAP.get((exch or "").upper())
        return canon, mcap
    except Exception:
        return None, None


def enrich_us(records: list[dict], want: set[str], *, lookup_mcap: bool) -> list[dict]:
    """Resolve each US row's exchange (via yfinance) and filter to `want`."""
    if not records:
        return []
    print(f"  Resolving exchange for {len(records)} US tickers via yfinance...")
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        future_to_rec = {ex.submit(_yf_lookup, r["symbol"]): r for r in records if r.get("symbol")}
        for fut in as_completed(future_to_rec):
            r = dict(future_to_rec[fut])
            exch, mcap = fut.result()
            r["exchange"] = exch or "UNKNOWN"
            if exch not in want:
                continue
            # Prefer NASDAQ-provided market cap; fall back to yfinance.
            parsed = _parse_market_cap(r.get("market_cap"))
            r["mcap_value"] = parsed if parsed is not None else (mcap if lookup_mcap else None)
            r["cap_tier"] = _cap_tier(r["mcap_value"])
            out.append(r)
    return out


def enrich_in(records: list[dict], *, lookup_mcap: bool) -> list[dict]:
    """Attach market-cap + tier to Indian records via yfinance."""
    out: list[dict] = []
    if not records:
        return out
    if lookup_mcap:
        syms = list({r["symbol"] for r in records if r.get("symbol")})
        print(f"  Fetching market caps for {len(syms)} Indian symbols via yfinance...")
        mcaps: dict[str, float | None] = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            for sym, fut in [(s, ex.submit(_yf_lookup, s)) for s in syms]:
                _, mcap = fut.result()
                mcaps[sym] = mcap
    else:
        mcaps = {}
    for r in records:
        r = dict(r)
        r["mcap_value"] = mcaps.get(r.get("symbol"))
        r["cap_tier"] = _cap_tier(r["mcap_value"])
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def category_key(record: dict) -> str:
    """e.g. ``nyse_2026-05-29_large-cap`` / ``bse_2026-05-30_small-cap``."""
    exch = (record.get("exchange") or "other").lower().replace(".", "")
    d    = str(record.get("date", "unknown"))
    tier = record.get("cap_tier", "unknown-cap")
    return f"{exch}_{d}_{tier}"


def build_watchlist(enriched: list[dict], trade_date_label: str) -> Watchlist:
    wl = Watchlist(trade_date=trade_date_label)
    for r in enriched:
        sym = r.get("symbol")
        if not sym:
            continue
        wl.add(category_key(r), sym)
    return wl


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_exchanges(arg: str | None) -> set[str]:
    if not arg:
        return set(ALL_EXCHANGES)
    requested = [x.strip().upper() for x in arg.split(",") if x.strip()]
    unknown = [x for x in requested if x not in ALL_EXCHANGES]
    if unknown:
        raise SystemExit(f"Unknown exchange(s): {unknown}. Valid: {ALL_EXCHANGES}")
    return set(requested)


def resolve_selection(region: str | None, exchanges_arg: str | None) -> tuple[set[str], str]:
    """Return (selected exchanges, label) honouring --exchanges over --region."""
    if exchanges_arg:
        return parse_exchanges(exchanges_arg), "custom"
    region = (region or "ALL").upper()
    if region not in REGIONS:
        raise SystemExit(f"Unknown --region {region!r}. Valid: {list(REGIONS)}")
    return set(REGIONS[region]), region.lower()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", choices=["US", "INDIA", "ALL"], default="ALL",
                    help="Convenience group selecting all exchanges in a region.")
    ap.add_argument("--exchanges",
                    help=f"Comma-separated subset of {ALL_EXCHANGES}. "
                         "Overrides --region when provided.")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--no-mcap", action="store_true",
                    help="Skip per-ticker market-cap lookup (faster).")
    args = ap.parse_args()

    want, region_label = resolve_selection(args.region, args.exchanges)
    lookup_mcap = not args.no_mcap
    today = date.today()
    end   = today + timedelta(days=args.days)
    print(f"Fetching earnings calendar from {today} to {end}")
    print(f"  Region: {region_label}   Exchanges: {sorted(want)}\n")

    us_rows: list[dict] = []
    in_rows: list[dict] = []

    # ---- US (NASDAQ feed covers NYSE/AMEX/ARCA/NASDAQ/BATS) ----
    if want & US_EXCHANGES:
        for i in range(args.days + 1):
            d = today + timedelta(days=i)
            if d.weekday() >= 5:  # skip weekends
                continue
            rows = fetch_nasdaq(d)
            print(f"  NASDAQ feed {d}: {len(rows)} tickers")
            us_rows.extend(rows)
            time.sleep(0.4)

    # ---- India ----
    if "NSE" in want:
        rows = fetch_nse(today, end)
        print(f"  NSE {today}..{end}: {len(rows)} events")
        in_rows.extend(rows)

    if "BSE" in want:
        rows = fetch_bse(today, end)
        print(f"  BSE {today}..{end}: {len(rows)} events")
        in_rows.extend(rows)

    print(f"\nRaw total: US={len(us_rows)}  India={len(in_rows)}")

    enriched = []
    if us_rows:
        enriched.extend(enrich_us(us_rows, want & US_EXCHANGES, lookup_mcap=lookup_mcap))
    if in_rows:
        enriched.extend(enrich_in(in_rows, lookup_mcap=lookup_mcap))

    print(f"Filtered/enriched: {len(enriched)} events across requested exchanges")

    if not enriched:
        print("No results — nothing to write.")
        return 0

    label = today.isoformat()  # must be ISO YYYY-MM-DD; analysts call strptime(trade_date)
    wl = build_watchlist(enriched, trade_date_label=label)
    fp = args.out or Path(f"earnings_watchlist_{region_label}_{today}.json")
    wl.save(fp)
    print(f"\nWrote {fp}")
    wl.print_summary()

    # Per-exchange tally
    tally: dict[str, int] = {}
    for r in enriched:
        tally[r.get("exchange", "?")] = tally.get(r.get("exchange", "?"), 0) + 1
    print("\nPer-exchange counts:")
    for k in sorted(tally):
        print(f"  {k:7s} {tally[k]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
