"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(str, Enum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(str, Enum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


# ---------------------------------------------------------------------------
# Scenario / ladder / data-quality models (used by Trader + Portfolio Manager)
# ---------------------------------------------------------------------------


class ScenarioRow(BaseModel):
    """One row in the probability-weighted scenario table."""

    label: str = Field(description="Scenario label, e.g. 'Bear', 'Base', 'Bull'.")
    probability: float = Field(
        description="Probability for this scenario in [0, 1]. Sum across rows must be 1.0 +/-0.01.",
        ge=0.0,
        le=1.0,
    )
    target_price: float = Field(
        description="12-month target price for this scenario, in the instrument's quote currency.",
    )
    logic: str = Field(
        description="One-line rationale: what has to be true for this scenario.",
    )


class ScenarioTable(BaseModel):
    """Probability-weighted scenario set. Probabilities must sum to 1.0 +/-0.01."""

    rows: List[ScenarioRow] = Field(
        description=(
            "Two or more scenarios (typically Bear / Base / Bull, optionally "
            "Disaster / Blue-sky)."
        ),
        min_length=2,
    )

    @model_validator(mode="after")
    def _probabilities_sum_to_one(self) -> "ScenarioTable":
        total = sum(r.probability for r in self.rows)
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"Scenario probabilities must sum to 1.0 +/-0.01; got {total:.4f}."
            )
        return self

    def weighted_target(self) -> float:
        return sum(r.probability * r.target_price for r in self.rows)


class SellLadderRung(BaseModel):
    """One rung of the sell ladder applied when the desk is currently long."""

    trigger: str = Field(
        description="Price level or condition that triggers this rung (e.g. '+25% from spot', '$8.00', 'breach $4 stop')."
    )
    action_pct: float = Field(
        description="Percent of remaining position to sell at this rung, in [0, 100].",
        ge=0.0,
        le=100.0,
    )
    rationale: str = Field(description="One-line rationale for this rung.")


class EntryRung(BaseModel):
    """One rung of the entry ladder applied when the desk is currently flat."""

    trigger: str = Field(description="Price level or condition that triggers this entry rung.")
    size_pct_of_intended: float = Field(
        description="Percent of intended total position to allocate at this rung, in [0, 100].",
        ge=0.0,
        le=100.0,
    )
    rationale: str = Field(description="One-line rationale for this rung.")


class DataQualityGrade(BaseModel):
    """Data-quality grade attached to the Portfolio Manager decision."""

    data_freshness: str = Field(description="Letter grade A-F for freshness of the quote and key inputs.")
    source_diversity_count: int = Field(description="Distinct data sources used.")
    microcap_mechanics_covered: bool = Field(
        description="True iff float / SI / borrow / runway were all present and used."
    )
    catalyst_timing_precision: str = Field(
        description="One of 'days' / 'weeks' / 'months' -- granularity of the next-catalyst clock."
    )
    reconciliation_status: str = Field(
        description="One of 'primary_cross_checked' / 'single_source' / 'unreconciled'."
    )


def render_scenario_table(table: ScenarioTable) -> str:
    """Render a scenario table as a markdown block."""
    lines = ["## Scenario Table", "", "| Scenario | Probability | 12-mo Target | Logic |", "|---|---|---|---|"]
    for r in table.rows:
        lines.append(
            f"| {r.label} | {r.probability*100:.0f}% | {r.target_price} | {r.logic} |"
        )
    lines.append("")
    lines.append(f"**Probability-weighted 12-mo target**: {table.weighted_target():.2f}")
    return "\n".join(lines)


def render_sell_ladder(rungs: List[SellLadderRung]) -> str:
    if not rungs:
        return ""
    lines = ["## Sell Ladder (if currently long)", "", "| Trigger | Action | Rationale |", "|---|---|---|"]
    for r in rungs:
        lines.append(f"| {r.trigger} | Sell {r.action_pct:.0f}% | {r.rationale} |")
    return "\n".join(lines)


def render_entry_ladder(rungs: List[EntryRung]) -> str:
    if not rungs:
        return ""
    lines = ["## Entry Ladder (if flat)", "", "| Trigger | Size | Rationale |", "|---|---|---|"]
    for r in rungs:
        lines.append(f"| {r.trigger} | {r.size_pct_of_intended:.0f}% of intended | {r.rationale} |")
    return "\n".join(lines)


def render_data_quality(grade: DataQualityGrade) -> str:
    return (
        "## Data-Quality Grade\n\n"
        f"- Freshness: **{grade.data_freshness}**\n"
        f"- Source diversity: {grade.source_diversity_count}\n"
        f"- Microcap mechanics covered: {grade.microcap_mechanics_covered}\n"
        f"- Catalyst-timing precision: {grade.catalyst_timing_precision}\n"
        f"- Reconciliation status: {grade.reconciliation_status}\n"
    )


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Reserve Hold for situations where the "
            "evidence on both sides is genuinely balanced; otherwise commit to "
            "the side with the stronger arguments."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including position sizing guidance consistent with the rating."
        ),
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    action: TraderAction = Field(
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: Optional[float] = Field(
        default=None,
        description="Optional entry price target in the instrument's quote currency.",
    )
    stop_loss: Optional[float] = Field(
        default=None,
        description="Optional stop-loss price in the instrument's quote currency.",
    )
    position_sizing: Optional[str] = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )
    entry_ladder: Optional[List[EntryRung]] = Field(
        default=None,
        description=(
            "Optional tiered entry ladder when flat. Prefer this over a single "
            "entry_price for microcap / volatile setups."
        ),
    )
    sell_ladder: Optional[List[SellLadderRung]] = Field(
        default=None,
        description=(
            "Optional tiered sell ladder when long. REQUIRED when the position "
            "is currently long AND the rating is Sell or Underweight."
        ),
    )


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.extend(["", f"**Entry Price**: {proposal.entry_price}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    if proposal.position_sizing:
        parts.extend(["", f"**Position Sizing**: {proposal.position_sizing}"])
    if proposal.entry_ladder:
        parts.extend(["", render_entry_ladder(proposal.entry_ladder)])
    if proposal.sell_ladder:
        parts.extend(["", render_sell_ladder(proposal.sell_ladder)])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    rating: PortfolioRating = Field(
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell, picked based on the analysts' debate."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis."
        ),
    )
    price_target: Optional[float] = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    time_horizon: Optional[str] = Field(
        default=None,
        description=(
            "Free-form recommended holding period. Prefer `time_horizon_days` "
            "for new code; kept for back-compat."
        ),
    )
    time_horizon_days: Optional[int] = Field(
        default=None,
        description=(
            "Recommended holding period in DAYS. Use <=15 when cash runway "
            "<9 months AND today's session move >100% (imminent dilution). "
            "Otherwise default to 30-90."
        ),
        ge=1,
    )
    scenarios: Optional[ScenarioTable] = Field(
        default=None,
        description=(
            "Probability-weighted scenario table (Bear / Base / Bull, optionally "
            "Disaster / Blue-sky). Probabilities must sum to 1.0 +/-0.01."
        ),
    )
    probability_weighted_12m_target: Optional[float] = Field(
        default=None,
        description=(
            "12-month probability-weighted target derived from the scenario table. "
            "Must equal sum(prob_i * target_i) within 1%."
        ),
    )
    implied_return_pct_from_spot: Optional[float] = Field(
        default=None,
        description="Implied return % from current spot to the probability-weighted target.",
    )
    sell_ladder_if_long: Optional[List[SellLadderRung]] = Field(
        default=None,
        description=(
            "Sell ladder for desks that already hold the security. REQUIRED "
            "when the rating is Sell or Underweight."
        ),
    )
    entry_ladder_if_flat: Optional[List[EntryRung]] = Field(
        default=None,
        description="Optional entry ladder for desks currently flat.",
    )
    data_quality: Optional[DataQualityGrade] = Field(
        default=None,
        description="Data-quality grade for this decision.",
    )

    @model_validator(mode="after")
    def _weighted_target_consistent(self) -> "PortfolioDecision":
        if self.scenarios is None or self.probability_weighted_12m_target is None:
            return self
        expected = self.scenarios.weighted_target()
        if expected == 0:
            return self
        if abs(self.probability_weighted_12m_target - expected) / abs(expected) > 0.01:
            raise ValueError(
                f"probability_weighted_12m_target ({self.probability_weighted_12m_target}) "
                f"does not match scenario-weighted sum ({expected:.4f})."
            )
        return self

    @model_validator(mode="after")
    def _sell_ladder_required_when_bearish(self) -> "PortfolioDecision":
        if self.rating in (PortfolioRating.SELL, PortfolioRating.UNDERWEIGHT):
            if not self.sell_ladder_if_long:
                raise ValueError(
                    "sell_ladder_if_long is REQUIRED when rating is Sell or Underweight."
                )
        return self


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    if decision.time_horizon_days is not None:
        parts.extend(["", f"**Time Horizon**: {decision.time_horizon_days} days"])
    elif decision.time_horizon:
        parts.extend(["", f"**Time Horizon**: {decision.time_horizon}"])
    if decision.scenarios is not None:
        parts.extend(["", render_scenario_table(decision.scenarios)])
    if decision.probability_weighted_12m_target is not None:
        parts.extend([
            "",
            f"**Probability-Weighted 12-mo Target**: {decision.probability_weighted_12m_target:.2f}",
        ])
    if decision.implied_return_pct_from_spot is not None:
        parts.extend([
            "",
            f"**Implied Return from Spot**: {decision.implied_return_pct_from_spot:+.1f}%",
        ])
    if decision.sell_ladder_if_long:
        parts.extend(["", render_sell_ladder(decision.sell_ladder_if_long)])
    if decision.entry_ladder_if_flat:
        parts.extend(["", render_entry_ladder(decision.entry_ladder_if_flat)])
    if decision.data_quality is not None:
        parts.extend(["", render_data_quality(decision.data_quality)])
    return "\n".join(parts)
