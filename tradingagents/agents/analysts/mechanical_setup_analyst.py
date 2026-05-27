"""MechanicalSetup analyst -- first node in the analyst pipeline.

Pulls float, short-interest dynamics, enterprise value, cash runway,
splits-last-5y, and an intraday freshness stamp via the
``get_microcap_mechanics`` tool. Pattern-matches the result against the
pump-archetype taxonomy and emits banners that downstream agents
(researchers, debaters, trader, portfolio manager) consume.

Slotted FIRST in ``selected_analysts`` so the mechanical regime is
established before any qualitative narrative analysis. For sub-$300M
US-listed equities this is the single most important data layer.
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_microcap_guardrails,
    get_microcap_mechanics,
)


def create_mechanical_setup_analyst(llm):
    def mechanical_setup_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        instrument_context = build_instrument_context(ticker)

        tools = [get_microcap_mechanics]

        system_message = (
            "You are the MechanicalSetup analyst. You run FIRST, before every "
            "narrative analyst, and your job is to anchor the rest of the team "
            "to the actual mechanical regime of the security.\n\n"
            "Workflow:\n"
            "1. Call `get_microcap_mechanics(ticker, curr_date)` exactly once.\n"
            "2. Read the structured JSON block inside the tool output to extract "
            "   `float_shares`, `short_interest_mom_change_pct`, `enterprise_value`, "
            "   `runway_months`, `intraday_change_pct`, `volume_multiple`, "
            "   `has_reverse_split_last_5y`, `quote_timestamp_iso`, "
            "   `freshness_age_minutes`, and `float_regime`.\n"
            "3. If `freshness_age_minutes` is missing or > 120, or if "
            "   `intraday_change_pct` exceeds +/- 10%, re-call the tool ONCE "
            "   to refresh the print before publishing.\n\n"
            "Your report MUST contain, in this exact order:\n"
            "1. Any banners from the tool output (MECHANICAL REGIME / IMMINENT "
            "   EQUITY RAISE) hoisted to the TOP, verbatim.\n"
            "2. A `## Mechanical Setup` section reproducing the tool's table.\n"
            "3. A `## Pump Archetype Check` section that pattern-matches against "
            "   known archetypes:\n"
            "   - Narrative pump (space / quantum / AI / crypto): float < 5M, "
            "     market cap < $50M, revenue YoY negative, runway < 9 months, a "
            "     thematic keyword in recent news. Base-rate 90-day retrace ~80%. "
            "     Exemplars: BNGO, BBIG, MULN, RDBX, ATER, GNS, AMTD, HKD.\n"
            "   - Reg-SHO squeeze tail: short interest > 20% of float, days-to-"
            "     cover > 5, MoM short-interest collapse > 50%.\n"
            "   - Reverse-merger dilution cycle: reverse split in last 5y, "
            "     repeated ATM offerings, runway < 6 months.\n"
            "   - Biotech catalyst binary: pre-revenue, catalyst date within "
            "     90 days, EV approaching cash value.\n"
            "   Declare match/non-match with explicit reasoning. State the "
            "   archetype name and its base-rate retrace if matched, or "
            "   'none' if no archetype fits.\n"
            "4. A `## Freshness & Reconciliation` section with the quote "
            "   timestamp, session label, age in minutes, and an explicit "
            "   statement of whether the data is fresh enough to act on.\n"
            "5. A `## Implications for Downstream Agents` section: a bulleted "
            "   list of constraints (compress time horizon to days, never quote "
            "   sell-side consensus when none exists, treat post-market microcap "
            "   prints as air, prefer defined-risk options over outright shorts "
            "   when float < 5M, etc.) -- only those that actually apply.\n\n"
            "Do NOT invent values. If the tool returns 'n/a' for a field, "
            "report it as 'n/a' and note the data gap in the Freshness section. "
            "Do NOT take post-market microcap prints as leading indicators. "
            "Always state the ticker explicitly."
            + get_language_instruction()
            + get_microcap_guardrails()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other "
                    "assistants. Use the provided tools to gather mechanical "
                    "data and write the MechanicalSetup report. "
                    "You have access to the following tools: {tool_names}.\n"
                    "{system_message}\n"
                    "For your reference, the current date is {current_date}. "
                    "{instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([t.name for t in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""
        archetype = "none"
        if len(result.tool_calls) == 0:
            report = result.content
            # Cheap heuristic: parse the archetype name out of the report.
            for name in (
                "Narrative pump",
                "Reg-SHO squeeze tail",
                "Reverse-merger dilution cycle",
                "Biotech catalyst binary",
            ):
                if name.lower() in (report or "").lower():
                    archetype = name
                    break

        return {
            "messages": [result],
            "mechanical_setup_report": report,
            "pump_archetype_match": archetype,
        }

    return mechanical_setup_analyst_node
