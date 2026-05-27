"""Unit tests for the scenario / sell-ladder / DQ schema additions."""

import pytest
from pydantic import ValidationError

from tradingagents.agents.schemas import (
    DataQualityGrade,
    EntryRung,
    PortfolioDecision,
    PortfolioRating,
    ScenarioRow,
    ScenarioTable,
    SellLadderRung,
    render_pm_decision,
)


def _ok_scenarios():
    return ScenarioTable(
        rows=[
            ScenarioRow(label="Bear", probability=0.5, target_price=4.0, logic="Retrace to pre-pump"),
            ScenarioRow(label="Base", probability=0.4, target_price=8.0, logic="Sticky narrative"),
            ScenarioRow(label="Bull", probability=0.1, target_price=20.0, logic="Squeeze continues"),
        ]
    )


def test_scenario_probabilities_must_sum_to_one():
    with pytest.raises(ValidationError):
        ScenarioTable(
            rows=[
                ScenarioRow(label="A", probability=0.3, target_price=1.0, logic="x"),
                ScenarioRow(label="B", probability=0.3, target_price=2.0, logic="y"),
            ]
        )


def test_scenario_weighted_target():
    table = _ok_scenarios()
    # 0.5*4 + 0.4*8 + 0.1*20 = 7.2
    assert table.weighted_target() == pytest.approx(7.2)


def test_sell_ladder_required_when_underweight():
    with pytest.raises(ValidationError):
        PortfolioDecision(
            rating=PortfolioRating.UNDERWEIGHT,
            executive_summary="x",
            investment_thesis="y",
            scenarios=_ok_scenarios(),
            probability_weighted_12m_target=7.2,
        )


def test_sell_ladder_present_passes_underweight():
    decision = PortfolioDecision(
        rating=PortfolioRating.UNDERWEIGHT,
        executive_summary="x",
        investment_thesis="y",
        scenarios=_ok_scenarios(),
        probability_weighted_12m_target=7.2,
        sell_ladder_if_long=[
            SellLadderRung(trigger="+10%", action_pct=50, rationale="lock partial gains"),
            SellLadderRung(trigger="break $4", action_pct=50, rationale="stop"),
        ],
    )
    assert decision.rating == PortfolioRating.UNDERWEIGHT


def test_weighted_target_mismatch_rejected():
    with pytest.raises(ValidationError):
        PortfolioDecision(
            rating=PortfolioRating.HOLD,
            executive_summary="x",
            investment_thesis="y",
            scenarios=_ok_scenarios(),
            probability_weighted_12m_target=100.0,  # way off
        )


def test_render_pm_includes_scenario_and_dq_blocks():
    decision = PortfolioDecision(
        rating=PortfolioRating.HOLD,
        executive_summary="x",
        investment_thesis="y",
        scenarios=_ok_scenarios(),
        probability_weighted_12m_target=7.2,
        implied_return_pct_from_spot=-25.0,
        time_horizon_days=10,
        data_quality=DataQualityGrade(
            data_freshness="A",
            source_diversity_count=4,
            microcap_mechanics_covered=True,
            catalyst_timing_precision="days",
            reconciliation_status="primary_cross_checked",
        ),
    )
    rendered = render_pm_decision(decision)
    assert "Scenario Table" in rendered
    assert "Probability-Weighted 12-mo Target" in rendered
    assert "Data-Quality Grade" in rendered
    assert "10 days" in rendered
