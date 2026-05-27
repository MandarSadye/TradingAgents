"""Pump archetype taxonomy + matcher.

Centralises the small set of recurring nano-/micro-cap setups that drive
>80% of parabolic moves so the MechanicalSetup analyst and downstream
agents apply consistent base-rate retrace probabilities rather than
re-invent the wheel per ticker.

Public API:
    ARCHETYPES: list[dict] -- name, criteria description, base-rate
        90-day retrace pct, exemplar tickers.
    match_archetype(mechanics, fundamentals, news) -> (name, reasoning)
        Returns the matched archetype name and a one-paragraph reasoning
        string. Returns ('none', explanation) when no archetype fits.

The matcher is intentionally simple and deterministic -- it consumes the
structured fields produced by ``microcap_mechanics.fetch_microcap_mechanics``
and optional fundamentals/news context. LLMs in the pipeline are still
free (and encouraged) to challenge a mismatch in their own reasoning;
this module just provides the consistent "first pass" pattern match.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional


THEMATIC_KEYWORDS = (
    "quantum",
    "lunar",
    "moon",
    "space",
    "ai ",
    " ai,",
    "artificial intelligence",
    "crypto",
    "bitcoin",
    "blockchain",
    "metaverse",
    "ev ",
    "electric vehicle",
    "psychedelic",
    "cannabis",
    "weight loss",
    "glp-1",
    "obesity",
)


ARCHETYPES = [
    {
        "name": "Narrative pump (space/quantum/AI/crypto)",
        "description": (
            "Nano-cap with a thematic keyword pumping the headline; "
            "no revenue trajectory to back it; runway forces a raise."
        ),
        "criteria": {
            "max_market_cap": 50_000_000,
            "max_float_shares": 5_000_000,
            "max_runway_months": 9,
            "thematic_keyword_required": True,
        },
        "base_rate_90d_retrace_pct": 80,
        "exemplars": ["BNGO", "BBIG", "MULN", "RDBX", "ATER", "GNS", "AMTD", "HKD"],
    },
    {
        "name": "Reg-SHO squeeze tail",
        "description": (
            "Heavily-shorted nano-/micro-cap where short interest collapsed "
            "month-over-month, days-to-cover elevated, mechanical squeeze risk."
        ),
        "criteria": {
            "min_short_pct_of_float": 20.0,
            "min_days_to_cover": 5.0,
            "min_short_mom_collapse_pct": -50.0,  # MoM SI change <= -50%
        },
        "base_rate_90d_retrace_pct": 70,
        "exemplars": ["GME", "AMC", "BBBYQ", "HKD"],
    },
    {
        "name": "Reverse-merger dilution cycle",
        "description": (
            "Recurring reverse split + ATM raise pattern. Reverse split in last "
            "5y plus runway under 6 months is a near-certain dilution event."
        ),
        "criteria": {
            "reverse_split_last_5y": True,
            "max_runway_months": 6,
        },
        "base_rate_90d_retrace_pct": 75,
        "exemplars": ["MULN", "BBIG", "NILE"],
    },
    {
        "name": "Biotech catalyst binary",
        "description": (
            "Pre-revenue biotech with EV approaching cash value and a "
            "binary catalyst (data readout, FDA decision) within ~90 days."
        ),
        "criteria": {
            "max_annualized_revenue": 5_000_000,
            "ev_below_cash": True,
        },
        "base_rate_90d_retrace_pct": 50,
        "exemplars": ["BDTX", "CERS"],
    },
]


def _has_thematic_keyword(news_items: Optional[Iterable[Any]]) -> tuple[bool, str]:
    """Return (matched, keyword) for any thematic keyword in news headlines."""
    if not news_items:
        return False, ""
    for item in news_items:
        text = ""
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = " ".join(
                str(item.get(k, "")) for k in ("title", "headline", "summary")
            )
        else:
            text = str(item)
        text = text.lower()
        for kw in THEMATIC_KEYWORDS:
            if kw in text:
                return True, kw.strip()
    return False, ""


def match_archetype(
    mechanics: dict,
    fundamentals: Optional[dict] = None,
    news: Optional[Iterable[Any]] = None,
) -> tuple[str, str]:
    """Match a ticker against the archetype taxonomy.

    ``mechanics`` is the ``asdict(MicrocapMechanics)`` payload from the
    ``microcap_mechanics`` dataflow. ``fundamentals`` and ``news`` are
    optional context. Returns ``(archetype_name, reasoning)``. When no
    archetype matches, returns ``("none", explanation)``.
    """
    fundamentals = fundamentals or {}
    market_cap = mechanics.get("market_cap")
    float_shares = mechanics.get("float_shares")
    runway = mechanics.get("runway_months")
    short_pct = mechanics.get("short_interest_pct_of_float")
    dtc = mechanics.get("days_to_cover")
    si_mom = mechanics.get("short_interest_mom_change_pct")
    has_rs = mechanics.get("has_reverse_split_last_5y", False)
    revenue = mechanics.get("annualized_revenue")
    ev = mechanics.get("enterprise_value")
    cash = mechanics.get("cash_plus_st_investments")

    matched_kw, kw = _has_thematic_keyword(news)

    # Archetype 1 -- Narrative pump
    crit = ARCHETYPES[0]["criteria"]
    if (
        (market_cap is not None and market_cap < crit["max_market_cap"])
        and (float_shares is not None and float_shares < crit["max_float_shares"])
        and (runway is not None and runway < crit["max_runway_months"])
        and matched_kw
    ):
        return (
            ARCHETYPES[0]["name"],
            (
                f"Market cap ${market_cap/1e6:.1f}M < $50M, float {float_shares:,} "
                f"< 5M, runway {runway:.1f} months < 9, thematic keyword '{kw}' in "
                f"news. Base-rate 90-day retrace ~{ARCHETYPES[0]['base_rate_90d_retrace_pct']}%. "
                f"Exemplars: {', '.join(ARCHETYPES[0]['exemplars'])}."
            ),
        )

    # Archetype 2 -- Reg-SHO squeeze tail
    crit = ARCHETYPES[1]["criteria"]
    if (
        (short_pct is not None and short_pct >= crit["min_short_pct_of_float"])
        and (dtc is not None and dtc >= crit["min_days_to_cover"])
        and (si_mom is not None and si_mom <= crit["min_short_mom_collapse_pct"])
    ):
        return (
            ARCHETYPES[1]["name"],
            (
                f"Short interest {short_pct:.1f}% of float, days-to-cover {dtc:.1f}, "
                f"MoM SI change {si_mom:.1f}% (collapse). Base-rate 90-day retrace "
                f"~{ARCHETYPES[1]['base_rate_90d_retrace_pct']}%."
            ),
        )

    # Archetype 3 -- Reverse-merger dilution cycle
    crit = ARCHETYPES[2]["criteria"]
    if has_rs and runway is not None and runway < crit["max_runway_months"]:
        return (
            ARCHETYPES[2]["name"],
            (
                f"Reverse split in last 5y AND runway {runway:.1f} months < 6. "
                f"Recurring dilution-rescue pattern. Base-rate 90-day retrace "
                f"~{ARCHETYPES[2]['base_rate_90d_retrace_pct']}%."
            ),
        )

    # Archetype 4 -- Biotech catalyst binary (heuristic: revenue under $5M and EV<cash)
    crit = ARCHETYPES[3]["criteria"]
    ev_below_cash = (
        ev is not None and cash is not None and ev < cash * 0.5
    )
    if (
        revenue is not None
        and revenue < crit["max_annualized_revenue"]
        and ev_below_cash
    ):
        return (
            ARCHETYPES[3]["name"],
            (
                f"Pre-revenue (annual revenue ${revenue/1e6:.1f}M) with EV "
                f"${ev/1e6:.1f}M well below cash ${cash/1e6:.1f}M. Likely "
                f"binary catalyst pending. Base-rate 90-day retrace "
                f"~{ARCHETYPES[3]['base_rate_90d_retrace_pct']}%."
            ),
        )

    return (
        "none",
        "No archetype matched. Apply normal-cap valuation methodology.",
    )
