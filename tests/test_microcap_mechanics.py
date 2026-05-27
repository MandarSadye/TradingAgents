"""Unit tests for the microcap_mechanics dataflow module.

Tests target the pure / deterministic pieces — float regime classifier,
runway formula, percent-change helper — so they run fast and offline
(no yfinance calls).
"""

import math

import pytest

from tradingagents.dataflows import microcap_mechanics as mm


def test_classify_float_thresholds():
    assert mm._classify_float(None) == "unknown"
    assert mm._classify_float(1_000_000) == "nano"
    assert mm._classify_float(4_999_999) == "nano"
    assert mm._classify_float(5_000_000) == "micro"
    assert mm._classify_float(19_999_999) == "micro"
    assert mm._classify_float(20_000_000) == "normal"
    assert mm._classify_float(500_000_000) == "normal"


def test_runway_profitable_returns_inf():
    months, burn = mm._compute_runway_months(
        cash=10_000_000, annualized_revenue=20_000_000, annualized_opex=15_000_000
    )
    assert math.isinf(months)
    assert burn == -5_000_000  # net burn negative = profitable


def test_runway_burning_cash():
    # $5M cash, $0 revenue, $12M opex -> burn $1M/mo -> 5 months
    months, burn = mm._compute_runway_months(
        cash=5_000_000, annualized_revenue=0, annualized_opex=12_000_000
    )
    assert burn == 12_000_000
    assert months == pytest.approx(5.0, rel=1e-3)


def test_runway_missing_inputs_returns_none():
    months, burn = mm._compute_runway_months(None, 0, 1_000_000)
    assert months is None and burn is None
    months, burn = mm._compute_runway_months(1_000_000, 0, None)
    assert months is None and burn is None


def test_pct_change_guards_zero_and_none():
    assert mm._pct_change(None, 10) is None
    assert mm._pct_change(10, None) is None
    assert mm._pct_change(10, 0) is None
    assert mm._pct_change(76_000, 960_000) == pytest.approx(-92.08, rel=1e-3)


def test_render_emits_nano_banner_for_low_float():
    m = mm.MicrocapMechanics(ticker="ASTC", float_shares=1_300_000)
    m.float_regime = "nano"
    out = mm.render_microcap_mechanics(m)
    assert "MECHANICAL REGIME: NANO-FLOAT" in out


def test_render_emits_dilution_callout_when_runway_short_and_parabolic():
    m = mm.MicrocapMechanics(
        ticker="ASTC",
        float_shares=1_300_000,
        runway_months=5.0,
        intraday_change_pct=459.0,
    )
    m.float_regime = "nano"
    out = mm.render_microcap_mechanics(m)
    assert "IMMINENT EQUITY RAISE" in out


def test_render_skips_dilution_callout_when_runway_long():
    m = mm.MicrocapMechanics(
        ticker="AAPL",
        float_shares=15_000_000_000,
        runway_months=float("inf"),
        intraday_change_pct=150.0,
    )
    m.float_regime = "normal"
    out = mm.render_microcap_mechanics(m)
    assert "IMMINENT EQUITY RAISE" not in out
