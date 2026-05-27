"""Shared microcap guardrail prompt fragment.

Imported by every prompt-building agent (analysts, researchers, debaters,
trader, managers) so the same defensive rules ship in every system
message instead of being re-typed (and silently drifting) per file.

Modelled on the ``get_language_instruction`` helper pattern in
``agent_utils``: a single function returns the fragment, and any agent
that wants the guardrails just appends ``get_microcap_guardrails()`` to
its system message string.
"""

from __future__ import annotations


_GUARDRAILS = """

## Microcap Guardrails (apply whenever the security is a sub-$300M-market-cap US-listed equity)

- **No outright shorts when float < 5M shares.** Default to defined-risk
  long puts instead. Naked or borrowed shorts on a nano-float can
  lose 10x in a single session.
- **Never quote sell-side "consensus" when no sell-side coverage exists.**
  If `recommendationKey` is missing or equals `"none"` in the
  fundamentals output, state plainly: "no sell-side coverage."
- **Post-market microcap prints are air.** They do not lead the next
  session. Label them as such; never derive entries, stops, or scenario
  targets from them.
- **Always state the analyzed security explicitly at the top** of every
  report (ticker + exchange + company name).
- **Cite sources as markdown hyperlinks with fetch timestamps.** If a
  number cannot be sourced, say so -- do not fabricate.
- **Compress time horizons to days, not months, whenever the
  MechanicalSetup report shows an IMMINENT EQUITY RAISE banner.**
- **Reverse-split + parabolic move is a dilution tell.** Down-weight the
  bullish thesis accordingly.
"""


def get_microcap_guardrails() -> str:
    """Return the shared microcap guardrails prompt fragment.

    Always returns the full fragment -- the guardrails are cheap and
    harmless for non-microcap names (the rules are scoped to "when the
    security is microcap"), so we don't try to gate them on the ticker.
    """
    return _GUARDRAILS
