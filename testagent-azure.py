import os
import sys
import json
from pathlib import Path
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

# Load from environment variables or .env.enterprise file
# DO NOT hardcode API keys in code
from dotenv import load_dotenv
load_dotenv(".env.enterprise")

# Disable warnings from pydantic
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "azure"
config["quick_think_llm"] = "gpt-5.4"      # Your Azure deployment name for GPT-5.4 mini
config["deep_think_llm"] = "gpt-5.4-mini"         # Your Azure deployment name for GPT-5.4 pro

# Load watchlist config (pass filename as arg: python testagent-azure.py watchlist-india.json)
watchlist_file = sys.argv[1] if len(sys.argv) > 1 else "watchlist.json"
with open(Path(__file__).parent / watchlist_file) as f:
    watchlist = json.load(f)

tickers = list(dict.fromkeys(t for v in watchlist["categories"].values() for t in v.split()))
trade_date = watchlist["trade_date"]
MAX_WORKERS = watchlist.get("max_workers", 3)
OUTPUT_DIR = Path("reports") / trade_date


def analyze(ticker):
    """Run analysis for a single ticker in its own graph instance."""
    ta = TradingAgentsGraph(debug=False, config=config)
    final_state, decision = ta.propagate(ticker, trade_date)
    return ticker, final_state, decision


def print_report(ticker, final_state):
    """Print detailed report for a ticker."""
    print(f"\n{'='*60}")
    print(f"  DETAILED REPORT: {ticker} ({trade_date})")
    print(f"{'='*60}")

    print(f"\n{'─'*40}")
    print("  MARKET ANALYST REPORT")
    print(f"{'─'*40}")
    print(final_state.get("market_report", "N/A"))

    print(f"\n{'─'*40}")
    print("  SENTIMENT ANALYST REPORT")
    print(f"{'─'*40}")
    print(final_state.get("sentiment_report", "N/A"))

    print(f"\n{'─'*40}")
    print("  NEWS ANALYST REPORT")
    print(f"{'─'*40}")
    print(final_state.get("news_report", "N/A"))

    print(f"\n{'─'*40}")
    print("  FUNDAMENTALS ANALYST REPORT")
    print(f"{'─'*40}")
    print(final_state.get("fundamentals_report", "N/A"))

    print(f"\n{'─'*40}")
    print("  BULL vs BEAR DEBATE")
    print(f"{'─'*40}")
    debate = final_state.get("investment_debate_state", {})
    print(f"Judge Decision: {debate.get('judge_decision', 'N/A')}")

    print(f"\n{'─'*40}")
    print("  RESEARCH MANAGER PLAN")
    print(f"{'─'*40}")
    print(final_state.get("investment_plan", "N/A"))

    print(f"\n{'─'*40}")
    print("  TRADER PLAN")
    print(f"{'─'*40}")
    print(final_state.get("trader_investment_plan", "N/A"))

    print(f"\n{'─'*40}")
    print("  RISK MANAGEMENT DEBATE")
    print(f"{'─'*40}")
    risk = final_state.get("risk_debate_state", {})
    print(f"Judge Decision: {risk.get('judge_decision', 'N/A')}")

    print(f"\n{'─'*40}")
    print("  FINAL PORTFOLIO MANAGER DECISION")
    print(f"{'─'*40}")
    print(final_state.get("final_trade_decision", "N/A"))


def save_report(ticker, final_state, output_dir):
    """Save detailed analysis report to a file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / f"{ticker}.md"

    debate = final_state.get("investment_debate_state", {})
    risk = final_state.get("risk_debate_state", {})

    # Convert history items to strings properly (they may be LangChain message objects)
    bull = "".join(str(m) for m in debate.get("bull_history", [])) or "N/A"
    bear = "".join(str(m) for m in debate.get("bear_history", [])) or "N/A"
    aggressive = "".join(str(m) for m in risk.get("aggressive_history", [])) or "N/A"
    conservative = "".join(str(m) for m in risk.get("conservative_history", [])) or "N/A"
    neutral = "".join(str(m) for m in risk.get("neutral_history", [])) or "N/A"

    report = f"""# {ticker} — Analysis Report ({trade_date})

## Market Analyst Report
{final_state.get("market_report", "N/A")}

## Sentiment Analyst Report
{final_state.get("sentiment_report", "N/A")}

## News Analyst Report
{final_state.get("news_report", "N/A")}

## Fundamentals Analyst Report
{final_state.get("fundamentals_report", "N/A")}

## Bull vs Bear Debate
### Bull Arguments
{bull}

### Bear Arguments
{bear}

### Judge Decision
{debate.get("judge_decision", "N/A")}

## Research Manager Plan
{final_state.get("investment_plan", "N/A")}

## Trader Plan
{final_state.get("trader_investment_plan", "N/A")}

## Risk Management Debate
### Aggressive View
{aggressive}

### Conservative View
{conservative}

### Neutral View
{neutral}

### Judge Decision
{risk.get("judge_decision", "N/A")}

## Final Portfolio Manager Decision
{final_state.get("final_trade_decision", "N/A")}
"""
    filepath.write_text(report, encoding="utf-8")
    return filepath


# Run all tickers in parallel
results = {}
print(f"Analyzing {len(tickers)} tickers in parallel (max {MAX_WORKERS} workers)...")
print(f"Reports will be saved to: {OUTPUT_DIR.resolve()}\n")

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
    futures = {pool.submit(analyze, t): t for t in tickers}
    for future in as_completed(futures):
        ticker = futures[future]
        try:
            ticker, final_state, decision = future.result()
            results[ticker] = {"decision": decision, "state": final_state}
            filepath = save_report(ticker, final_state, OUTPUT_DIR)
            print(f"[{ticker}] Completed ✓ → {filepath}")
        except Exception as e:
            results[ticker] = {"decision": f"ERROR: {e}", "state": None}
            print(f"[{ticker}] Failed: {e}")

# Print all reports to console
for ticker in tickers:
    data = results.get(ticker)
    if data and data["state"]:
        print_report(ticker, data["state"])

print(f"\n{'='*60}")
print("  SUMMARY")
print(f"{'='*60}")
for ticker, data in results.items():
    print(f"\n{ticker}: {data['decision']}")
