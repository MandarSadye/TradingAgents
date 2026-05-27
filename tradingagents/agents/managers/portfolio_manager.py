"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call.  The result is rendered
back to markdown for storage in ``final_trade_decision`` so memory log,
CLI display, and saved reports continue to consume the same shape they do
today.  When a provider does not expose structured output, the agent falls
back gracefully to free-text generation.
"""

from __future__ import annotations

from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_microcap_guardrails,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"])

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]
        mechanical_report = state.get("mechanical_setup_report", "") or ""
        archetype_match = state.get("pump_archetype_match", "none")

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        mechanical_block = ""
        if mechanical_report:
            mechanical_block = (
                "**MechanicalSetup Report (CANONICAL -- overrides any conflicting "
                f"narrative claims):**\n{mechanical_report}\n\n"
                f"**Pump archetype match:** {archetype_match}\n"
            )

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}{mechanical_block}**Risk Analysts Debate History:**
{history}

---

## Required output (HARD)

You must fill the structured schema completely:

1. **scenarios**: A `ScenarioTable` with at least Bear / Base / Bull rows.
   Probabilities MUST sum to 1.0 +/-0.01. Each row needs a 12-month target
   price and a one-line `logic` field.
2. **probability_weighted_12m_target**: sum(prob_i * target_i). Must
   match the scenario table within 1% or the schema validator will reject.
3. **implied_return_pct_from_spot**: (weighted_target / spot - 1) * 100,
   where `spot` is the live price reported in the MechanicalSetup report.
4. **time_horizon_days**: integer days. If the MechanicalSetup report
   shows `runway_months < 9` AND today's session move > 100%, this MUST
   be <= 15 (imminent dilution clock). Otherwise default to 30-90.
5. **sell_ladder_if_long**: REQUIRED whenever rating is Sell or
   Underweight. Provide at least two rungs with explicit triggers,
   percentages of remaining position to sell, and rationale.
6. **entry_ladder_if_flat**: Optional but recommended for Buy /
   Overweight ratings on volatile names.
7. **data_quality**: Fill a `DataQualityGrade` block:
   - `data_freshness`: A-F based on quote freshness (A = live intraday print
     within 15 minutes; F = stale by >24h).
   - `source_diversity_count`: distinct data sources actually used.
   - `microcap_mechanics_covered`: True iff the MechanicalSetup report was
     present AND its float / SI / runway fields were non-null.
   - `catalyst_timing_precision`: 'days' / 'weeks' / 'months'.
   - `reconciliation_status`: 'primary_cross_checked' / 'single_source' /
     'unreconciled'.

Be decisive and ground every conclusion in specific evidence from the analysts.{get_language_instruction()}{get_microcap_guardrails()}"""

        final_trade_decision = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_pm_decision,
            "Portfolio Manager",
        )

        new_risk_debate_state = {
            "judge_decision": final_trade_decision,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
            "data_quality_grade": final_trade_decision,
        }

    return portfolio_manager_node
