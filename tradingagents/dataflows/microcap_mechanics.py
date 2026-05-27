"""Microcap mechanics extractor.

Returns float, short-interest dynamics, enterprise value, runway, splits,
volume multiple, and a freshness stamp for a single ticker. These are the
variables that explain >80% of nano-cap price action and the variables
that the rest of the TradingAgent pipeline (MechanicalSetup analyst,
pump-archetype matcher, Portfolio Manager scenario tables) consumes
downstream.

Single source-of-truth path: ``yfinance.Ticker.info`` /
``.fast_info`` / ``.splits`` / ``.quarterly_financials`` /
``.history(period="2d", prepost=True)``. Mirrors what ``y_finance.py``
already does so caching and retry behaviour are consistent.

The public entry point is :func:`get_microcap_mechanics`. Returns a
formatted markdown report (string) plus an embedded JSON block so
downstream agents can parse the structured fields without re-running the
fetch.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

import pandas as pd
import yfinance as yf

from .stockstats_utils import yf_retry


# ---------------------------------------------------------------------------
# Thresholds (mirrored in pump_archetypes.py -- keep in sync)
# ---------------------------------------------------------------------------

NANO_FLOAT_THRESHOLD = 5_000_000        # <5M shares = "nano" mechanical regime
MICRO_FLOAT_THRESHOLD = 20_000_000      # 5-20M = "micro"; >20M = "normal"
LOW_RUNWAY_MONTHS = 9                   # <9mo runway triggers dilution flag
PARABOLA_SESSION_MOVE_PCT = 100.0       # >100% session move = parabolic
STALENESS_MINUTES = 120                 # quote older than this is stale


# ---------------------------------------------------------------------------
# Structured return type
# ---------------------------------------------------------------------------


@dataclass
class MicrocapMechanics:
    """Structured microcap mechanics for a single ticker.

    Every numeric field is ``Optional`` because yfinance regularly omits
    fields for thin tickers; downstream agents must handle ``None``
    gracefully rather than assume completeness.
    """

    ticker: str
    # Float / shares
    float_shares: Optional[int] = None
    shares_outstanding: Optional[int] = None
    float_pct_of_so: Optional[float] = None
    float_regime: str = "unknown"          # nano / micro / normal / unknown
    # Short interest
    shares_short: Optional[int] = None
    shares_short_prior_month: Optional[int] = None
    short_interest_pct_of_float: Optional[float] = None
    days_to_cover: Optional[float] = None
    short_interest_mom_change_pct: Optional[float] = None
    # Capital structure
    market_cap: Optional[float] = None
    enterprise_value: Optional[float] = None
    cash_plus_st_investments: Optional[float] = None
    total_debt: Optional[float] = None
    # Volume
    avg_volume_10d: Optional[float] = None
    session_volume: Optional[int] = None
    volume_multiple: Optional[float] = None
    # Price / freshness
    quote_timestamp_iso: Optional[str] = None
    quote_session: str = "unknown"         # regular / post / pre / closed / unknown
    last_regular_price: Optional[float] = None
    last_post_market_price: Optional[float] = None
    prior_close: Optional[float] = None
    intraday_change_pct: Optional[float] = None
    freshness_age_minutes: Optional[float] = None
    # Burn / runway
    annualized_revenue: Optional[float] = None
    annualized_opex: Optional[float] = None
    annualized_net_burn: Optional[float] = None
    runway_months: Optional[float] = None
    # Capital-structure history
    splits_last_5y: list = field(default_factory=list)
    has_reverse_split_last_5y: bool = False
    # Diagnostics
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe(d: Any, key: str, default=None):
    """yfinance ``info`` is a dict but can be ``None``; guard accessor."""
    if not d:
        return default
    val = d.get(key, default)
    if val is None:
        return default
    return val


def _to_int(val):
    try:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return None
        return int(val)
    except (TypeError, ValueError):
        return None


def _to_float(val):
    try:
        if val is None:
            return None
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _classify_float(float_shares: Optional[int]) -> str:
    if float_shares is None:
        return "unknown"
    if float_shares < NANO_FLOAT_THRESHOLD:
        return "nano"
    if float_shares < MICRO_FLOAT_THRESHOLD:
        return "micro"
    return "normal"


def _pct_change(curr: Optional[float], prior: Optional[float]) -> Optional[float]:
    if curr is None or prior is None or prior == 0:
        return None
    return (curr - prior) / prior * 100.0


def _annualize_quarterly(df: pd.DataFrame, row_keys: list) -> Optional[float]:
    """Sum the four most recent quarterly values for any of the given row keys.

    Returns ``None`` when fewer than two quarters are available or the
    row is missing entirely. yfinance quarterly statements use fiscal
    period end dates as columns; we sum the last four columns.
    """
    if df is None or df.empty:
        return None
    for key in row_keys:
        if key in df.index:
            series = df.loc[key].dropna()
            if series.empty:
                continue
            # Take up to last 4 quarters; if fewer, scale up
            recent = series.iloc[: min(4, len(series))]
            total = float(recent.sum())
            if len(recent) < 4 and len(recent) > 0:
                total = total * (4 / len(recent))
            return total
    return None


def _compute_runway_months(
    cash: Optional[float],
    annualized_revenue: Optional[float],
    annualized_opex: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """Return (runway_months, annualized_net_burn).

    ``net_burn = opex - revenue`` (positive => burning cash).
    ``runway = cash / (net_burn / 12)`` only when both are positive and finite.
    """
    if cash is None or annualized_opex is None:
        return None, None
    revenue = annualized_revenue or 0.0
    net_burn = annualized_opex - revenue
    if net_burn <= 0:
        return float("inf"), net_burn  # profitable / breakeven
    months = cash / (net_burn / 12.0)
    return months, net_burn


def _classify_quote_session(now_utc: datetime) -> str:
    """Best-effort label for which US-equities session we are in.

    Uses naive Eastern-time approximation (UTC-5; ignores DST shifts and
    half-days). Good enough for a "regular/post/pre/closed" label that an
    LLM will then reconcile against the actual quote timestamp.
    """
    # Convert to a naive Eastern approximation
    eastern = now_utc.replace(tzinfo=None) - pd.Timedelta(hours=5)
    if eastern.weekday() >= 5:
        return "closed"
    h, m = eastern.hour, eastern.minute
    mins = h * 60 + m
    if 9 * 60 + 30 <= mins < 16 * 60:
        return "regular"
    if 4 * 60 <= mins < 9 * 60 + 30:
        return "pre"
    if 16 * 60 <= mins < 20 * 60:
        return "post"
    return "closed"


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------


def fetch_microcap_mechanics(
    ticker: str,
    curr_date: Optional[str] = None,
) -> MicrocapMechanics:
    """Pull every microcap-mechanics field for ``ticker``.

    ``curr_date`` is accepted for symmetry with other dataflow methods
    but is not used as a backtest cutoff for this extractor -- float,
    short interest, and runway are point-in-time fields that yfinance
    only exposes "as of now". Callers running historical backtests
    should be aware of this limitation; it is surfaced in the rendered
    output via the ``quote_timestamp_iso`` field.
    """
    sym = ticker.upper().strip()
    m = MicrocapMechanics(ticker=sym)

    try:
        ticker_obj = yf.Ticker(sym)
        info = yf_retry(lambda: ticker_obj.info) or {}
    except Exception as exc:
        m.warnings.append(f"yfinance .info fetch failed: {exc}")
        info = {}

    # --- Float / shares -----------------------------------------------------
    m.float_shares = _to_int(_safe(info, "floatShares"))
    m.shares_outstanding = _to_int(_safe(info, "sharesOutstanding"))
    if m.float_shares and m.shares_outstanding:
        m.float_pct_of_so = m.float_shares / m.shares_outstanding * 100.0
    m.float_regime = _classify_float(m.float_shares)

    # --- Short interest -----------------------------------------------------
    m.shares_short = _to_int(_safe(info, "sharesShort"))
    m.shares_short_prior_month = _to_int(_safe(info, "sharesShortPriorMonth"))
    if m.shares_short is not None and m.float_shares:
        m.short_interest_pct_of_float = m.shares_short / m.float_shares * 100.0
    m.days_to_cover = _to_float(_safe(info, "shortRatio"))
    m.short_interest_mom_change_pct = _pct_change(
        m.shares_short, m.shares_short_prior_month
    )

    # --- Capital structure --------------------------------------------------
    m.market_cap = _to_float(_safe(info, "marketCap"))
    m.enterprise_value = _to_float(_safe(info, "enterpriseValue"))
    cash = _to_float(_safe(info, "totalCash"))
    if cash is None:
        cash = _to_float(_safe(info, "cash"))
    m.cash_plus_st_investments = cash
    m.total_debt = _to_float(_safe(info, "totalDebt"))

    # --- Volume / price -----------------------------------------------------
    m.avg_volume_10d = _to_float(_safe(info, "averageDailyVolume10Day"))
    if m.avg_volume_10d is None:
        m.avg_volume_10d = _to_float(_safe(info, "averageVolume10days"))

    # Try a 2-day history including pre/post to capture the most recent print.
    try:
        hist = yf_retry(
            lambda: ticker_obj.history(period="2d", prepost=True, auto_adjust=False)
        )
    except Exception as exc:
        m.warnings.append(f"yfinance .history fetch failed: {exc}")
        hist = pd.DataFrame()

    if not hist.empty:
        last = hist.iloc[-1]
        m.session_volume = _to_int(last.get("Volume"))
        m.last_regular_price = _to_float(last.get("Close"))
        # Prior close is the row before the latest
        if len(hist) >= 2:
            m.prior_close = _to_float(hist.iloc[-2].get("Close"))
        # Timestamp of the last bar (already timezone-aware from yfinance)
        ts = hist.index[-1]
        try:
            m.quote_timestamp_iso = pd.Timestamp(ts).tz_convert("UTC").isoformat()
        except (TypeError, ValueError):
            m.quote_timestamp_iso = pd.Timestamp(ts).isoformat()

    # post-market price from info if exposed
    m.last_post_market_price = _to_float(_safe(info, "postMarketPrice"))

    if m.last_regular_price and m.prior_close:
        m.intraday_change_pct = _pct_change(m.last_regular_price, m.prior_close)

    if m.session_volume and m.avg_volume_10d:
        m.volume_multiple = m.session_volume / m.avg_volume_10d

    # Freshness age (vs. now UTC)
    now_utc = datetime.now(timezone.utc)
    m.quote_session = _classify_quote_session(now_utc)
    if m.quote_timestamp_iso:
        try:
            ts = pd.Timestamp(m.quote_timestamp_iso)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            age = (now_utc - ts.to_pydatetime()).total_seconds() / 60.0
            m.freshness_age_minutes = age
            if age > STALENESS_MINUTES:
                m.warnings.append(
                    f"Quote is {age:.0f} min old (> {STALENESS_MINUTES} min staleness threshold)."
                )
        except Exception:
            pass

    # --- Burn / runway ------------------------------------------------------
    try:
        q_income = yf_retry(lambda: ticker_obj.quarterly_income_stmt)
    except Exception as exc:
        m.warnings.append(f"quarterly_income_stmt fetch failed: {exc}")
        q_income = pd.DataFrame()

    m.annualized_revenue = _annualize_quarterly(
        q_income, ["Total Revenue", "TotalRevenue", "Revenue"]
    )
    m.annualized_opex = _annualize_quarterly(
        q_income, ["Operating Expense", "OperatingExpense", "Total Operating Expenses"]
    )
    # Fallback: derive opex from revenue - operating income if opex row missing
    if m.annualized_opex is None:
        op_income = _annualize_quarterly(
            q_income, ["Operating Income", "OperatingIncome"]
        )
        if op_income is not None and m.annualized_revenue is not None:
            m.annualized_opex = m.annualized_revenue - op_income

    m.runway_months, m.annualized_net_burn = _compute_runway_months(
        m.cash_plus_st_investments, m.annualized_revenue, m.annualized_opex
    )

    # --- Splits last 5y -----------------------------------------------------
    try:
        splits = yf_retry(lambda: ticker_obj.splits)
    except Exception:
        splits = pd.Series(dtype=float)
    if splits is not None and not splits.empty:
        cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.DateOffset(years=5)
        for dt, ratio in splits.items():
            dt_naive = pd.Timestamp(dt).tz_localize(None) if pd.Timestamp(dt).tzinfo else pd.Timestamp(dt)
            if dt_naive >= cutoff:
                m.splits_last_5y.append(
                    {"date": dt_naive.strftime("%Y-%m-%d"), "ratio": float(ratio)}
                )
                # Reverse split: ratio < 1 (e.g. 1:30 reported as ~0.0333)
                if float(ratio) < 1.0:
                    m.has_reverse_split_last_5y = True

    return m


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _fmt_int(v):
    return f"{v:,}" if isinstance(v, (int, float)) and v is not None else "n/a"


def _fmt_pct(v, digits=2):
    if v is None:
        return "n/a"
    if isinstance(v, float) and math.isinf(v):
        return "inf"
    return f"{v:.{digits}f}%"


def _fmt_money(v):
    if v is None:
        return "n/a"
    av = abs(v)
    if av >= 1e9:
        return f"${v/1e9:.2f}B"
    if av >= 1e6:
        return f"${v/1e6:.2f}M"
    if av >= 1e3:
        return f"${v/1e3:.1f}K"
    return f"${v:.0f}"


def _fmt_float(v, digits=2):
    if v is None:
        return "n/a"
    if isinstance(v, float) and math.isinf(v):
        return "inf"
    return f"{v:.{digits}f}"


def render_microcap_mechanics(m: MicrocapMechanics) -> str:
    """Render a :class:`MicrocapMechanics` as a markdown report.

    The report leads with a banner when the ticker is in the ``nano``
    float regime, and emits an "IMMINENT EQUITY RAISE" callout when
    runway is short and the session move is parabolic. Downstream agents
    are instructed (in the MechanicalSetup analyst prompt) to surface
    those banners at the top of their own output.
    """
    parts: list[str] = []

    # ---- Banners ----
    if m.float_regime == "nano":
        parts.append(
            f"> **MECHANICAL REGIME: NANO-FLOAT** -- float {_fmt_int(m.float_shares)} shares "
            f"(< {NANO_FLOAT_THRESHOLD:,}). Different rules apply: thin order book, "
            "asymmetric short borrow, headline-driven parabolas, and dilutive raises "
            "are the dominant variables."
        )

    if (
        m.runway_months is not None
        and not math.isinf(m.runway_months)
        and m.runway_months < LOW_RUNWAY_MONTHS
        and m.intraday_change_pct is not None
        and m.intraday_change_pct > PARABOLA_SESSION_MOVE_PCT
    ):
        parts.append(
            f"> **IMMINENT EQUITY RAISE -- resolves in 1-15 trading days.** "
            f"Cash runway ~= {_fmt_float(m.runway_months, 1)} months and session is up "
            f"{_fmt_pct(m.intraday_change_pct)}. Compress the time horizon to days, "
            "not months."
        )

    # ---- Header ----
    parts.append(f"# Microcap Mechanics -- {m.ticker}\n")
    if m.quote_timestamp_iso:
        parts.append(
            f"**As of:** {m.quote_timestamp_iso} ({m.quote_session} session; "
            f"data age {_fmt_float(m.freshness_age_minutes, 0)} min)\n"
        )
    else:
        parts.append("**As of:** (quote timestamp unavailable)\n")

    # ---- Mechanical setup table ----
    parts.append("## Mechanical setup\n")
    parts.append("| Metric | Value | Note |")
    parts.append("|---|---|---|")
    parts.append(
        f"| Float | {_fmt_int(m.float_shares)} sh | "
        f"{_fmt_pct(m.float_pct_of_so, 1)} of shares outstanding; regime = **{m.float_regime}** |"
    )
    parts.append(
        f"| Shares outstanding | {_fmt_int(m.shares_outstanding)} sh | |"
    )
    parts.append(
        f"| Short interest | {_fmt_int(m.shares_short)} sh | "
        f"{_fmt_pct(m.short_interest_pct_of_float, 1)} of float; "
        f"days-to-cover {_fmt_float(m.days_to_cover, 2)} |"
    )
    parts.append(
        f"| Short interest (prior month) | {_fmt_int(m.shares_short_prior_month)} sh | "
        f"MoM change {_fmt_pct(m.short_interest_mom_change_pct, 1)} |"
    )
    parts.append(
        f"| Market cap | {_fmt_money(m.market_cap)} | |"
    )
    parts.append(
        f"| Enterprise value | {_fmt_money(m.enterprise_value)} | "
        f"{'(!) EV is negative -- market pricing the business at less than net cash' if (m.enterprise_value is not None and m.enterprise_value < 0) else ''} |"
    )
    parts.append(
        f"| Cash + ST investments | {_fmt_money(m.cash_plus_st_investments)} | |"
    )
    parts.append(
        f"| Total debt | {_fmt_money(m.total_debt)} | |"
    )
    parts.append(
        f"| Session volume | {_fmt_int(m.session_volume)} | "
        f"{_fmt_float(m.volume_multiple, 1)}x the 10-day avg ({_fmt_int(m.avg_volume_10d)}) |"
    )
    parts.append(
        f"| Last regular price | {_fmt_money(m.last_regular_price)} | "
        f"intraday {_fmt_pct(m.intraday_change_pct, 2)} vs prior close {_fmt_money(m.prior_close)} |"
    )
    parts.append(
        f"| Post-market | {_fmt_money(m.last_post_market_price)} | "
        "(microcap post-market prints are air; not a leading indicator) |"
    )
    parts.append(
        f"| Annualized revenue | {_fmt_money(m.annualized_revenue)} | trailing 4Q |"
    )
    parts.append(
        f"| Annualized opex | {_fmt_money(m.annualized_opex)} | trailing 4Q |"
    )
    parts.append(
        f"| Annualized net burn | {_fmt_money(m.annualized_net_burn)} | opex - revenue |"
    )
    parts.append(
        f"| **Cash runway** | **{_fmt_float(m.runway_months, 1)} months** | "
        f"{'(!) < 9mo -- dilution risk dominates' if (m.runway_months is not None and not math.isinf(m.runway_months) and m.runway_months < LOW_RUNWAY_MONTHS) else ''} |"
    )

    # ---- Splits last 5y ----
    parts.append("\n## Capital-structure history (splits, last 5y)\n")
    if not m.splits_last_5y:
        parts.append("- No stock splits in the last 5 years.")
    else:
        for s in m.splits_last_5y:
            kind = "REVERSE SPLIT" if s["ratio"] < 1.0 else "forward split"
            # Convert ratio back to a readable form (1:30 etc.)
            if s["ratio"] < 1.0 and s["ratio"] > 0:
                readable = f"1:{round(1.0/s['ratio'])}"
            else:
                readable = f"{s['ratio']}:1"
            parts.append(f"- **{s['date']}** -- {kind} ({readable}, ratio={s['ratio']})")
        if m.has_reverse_split_last_5y:
            parts.append(
                "\n> (!) Reverse split in the last 5 years is a tell for a recurring "
                "dilution-rescue cycle. Weight the dilution-risk score accordingly."
            )

    # ---- Warnings / diagnostics ----
    if m.warnings:
        parts.append("\n## Data warnings\n")
        for w in m.warnings:
            parts.append(f"- {w}")

    # ---- Embedded structured JSON for downstream consumption ----
    parts.append("\n## Structured fields (for downstream agents)\n")
    parts.append("```json")
    parts.append(json.dumps(asdict(m), indent=2, default=str))
    parts.append("```")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Vendor-routed entry point (consumed by interface.route_to_vendor)
# ---------------------------------------------------------------------------


def get_microcap_mechanics(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current trading date, yyyy-mm-dd"] = None,
) -> str:
    """Top-level callable returned by ``route_to_vendor("get_microcap_mechanics", ...)``."""
    try:
        m = fetch_microcap_mechanics(ticker, curr_date)
    except Exception as exc:  # noqa: BLE001
        return f"Error retrieving microcap mechanics for {ticker}: {exc}"
    return render_microcap_mechanics(m)
