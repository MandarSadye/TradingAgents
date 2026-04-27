from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "ollama"                           # OpenAI-compatible mode
config["backend_url"] = "http://127.0.0.1:3030/v1"          # Copilot API Gateway
config["quick_think_llm"] = "claude-opus-4.7"
config["deep_think_llm"] = "claude-opus-4.7"

tickers = ["NVDA", "AAPL", "MSFT", "GOOGL", "TSLA"]
trade_date = "2026-04-25"

ta = TradingAgentsGraph(debug=True, config=config)

results = {}
for ticker in tickers:
    print(f"\n{'='*60}")
    print(f"  Analyzing {ticker} on {trade_date}")
    print(f"{'='*60}\n")
    try:
        final_state, decision = ta.propagate(ticker, trade_date)
        results[ticker] = {"decision": decision, "state": final_state}

        # Print detailed report
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

    except Exception as e:
        print(f"\n[{ticker}] Failed: {e}\n")
        results[ticker] = {"decision": f"ERROR: {e}", "state": None}

print(f"\n{'='*60}")
print("  SUMMARY")
print(f"{'='*60}")
for ticker, data in results.items():
    print(f"\n{ticker}: {data['decision']}")