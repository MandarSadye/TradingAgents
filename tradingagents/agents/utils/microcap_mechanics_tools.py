"""Tool wrapper for the microcap-mechanics dataflow.

Exposes a single LangChain ``@tool`` that the MechanicalSetup analyst
binds. Routes via :func:`tradingagents.dataflows.interface.route_to_vendor`
under the ``microcap_mechanics`` category so vendor selection follows the
same configuration model as every other data category.
"""

from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_microcap_mechanics(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """Retrieve microcap mechanics for a ticker: float, short interest
    (and MoM change), enterprise value, cash runway in months, recent
    splits, volume vs 10-day average, and a freshness-stamped intraday
    price/change.

    The report leads with a ``MECHANICAL REGIME: NANO-FLOAT`` banner when
    float < 5M and an ``IMMINENT EQUITY RAISE`` callout when cash runway
    is short and the session is parabolic. Use this tool FIRST for any
    sub-$300M-market-cap US-listed equity.

    Args:
        ticker: Ticker symbol of the company.
        curr_date: Current trading date (yyyy-mm-dd). Note: float and
            short-interest are point-in-time fields from yfinance — they
            reflect the latest published value, not a historical snapshot.

    Returns:
        Markdown report with banners, a mechanical-setup table, capital-
        structure history, and an embedded JSON block of structured fields
        for downstream agents.
    """
    return route_to_vendor("get_microcap_mechanics", ticker, curr_date)
