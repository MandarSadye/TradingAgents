"""Unit tests for the pump-archetype matcher."""

from tradingagents.agents.utils import pump_archetypes as pa


def _astc_shape_mechanics():
    """ASTC on 2026-05-28 — the canonical narrative-pump test fixture."""
    return {
        "market_cap": 24_000_000,
        "float_shares": 1_306_057,
        "runway_months": 5.0,
        "short_interest_pct_of_float": 5.8,
        "days_to_cover": 0.5,
        "short_interest_mom_change_pct": -92.0,
        "has_reverse_split_last_5y": True,
        "annualized_revenue": 800_000,
        "enterprise_value": -62_000,
        "cash_plus_st_investments": 5_500_000,
    }


def _aapl_shape_mechanics():
    return {
        "market_cap": 3_000_000_000_000,
        "float_shares": 15_000_000_000,
        "runway_months": float("inf"),
        "short_interest_pct_of_float": 0.7,
        "days_to_cover": 1.0,
        "short_interest_mom_change_pct": 1.0,
        "has_reverse_split_last_5y": False,
        "annualized_revenue": 400_000_000_000,
        "enterprise_value": 2_900_000_000_000,
        "cash_plus_st_investments": 50_000_000_000,
    }


def test_astc_matches_narrative_pump():
    news = [{"title": "Astrotech to unveil quantum mass spectrometer for lunar mission"}]
    name, reasoning = pa.match_archetype(_astc_shape_mechanics(), news=news)
    assert name == "Narrative pump (space/quantum/AI/crypto)"
    assert "quantum" in reasoning.lower() or "lunar" in reasoning.lower()


def test_aapl_matches_no_archetype():
    name, reasoning = pa.match_archetype(_aapl_shape_mechanics(), news=[])
    assert name == "none"


def test_reg_sho_squeeze_tail_matches():
    mechanics = {
        "market_cap": 1_000_000_000,
        "float_shares": 50_000_000,
        "runway_months": 24.0,
        "short_interest_pct_of_float": 35.0,
        "days_to_cover": 8.0,
        "short_interest_mom_change_pct": -60.0,
        "has_reverse_split_last_5y": False,
        "annualized_revenue": 100_000_000,
        "enterprise_value": 800_000_000,
        "cash_plus_st_investments": 200_000_000,
    }
    name, _ = pa.match_archetype(mechanics, news=[])
    assert name == "Reg-SHO squeeze tail"


def test_thematic_keyword_detection():
    found, kw = pa._has_thematic_keyword([{"title": "Company unveils Bitcoin treasury strategy"}])
    assert found and "bitcoin" in kw.lower()
    found, _ = pa._has_thematic_keyword([{"title": "Quarterly earnings beat by 2 cents"}])
    assert not found
