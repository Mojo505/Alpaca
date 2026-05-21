"""
Quick test for Agent 2 — Analyst.
Runs the scanner first, then scores the results.

Run: python test_analyst.py
"""
from agents.scanner import scan_market
from agents.analyst import analyze, _print_results

if __name__ == "__main__":
    print("=" * 60)
    print("  Testing Agent 2 — Analyst")
    print("  Indicators: RSI | EMA | MA | Fibonacci Retracement")
    print("=" * 60 + "\n")

    symbols = scan_market()

    if not symbols:
        print("ERROR: Scanner returned no symbols. Fix Agent 1 first.")
        exit(1)

    results = analyze(symbols)

    if not results:
        print("ERROR: Analyst returned no results. Check API keys and data.")
        exit(1)

    _print_results(results)

    print(f"SUCCESS — {len(results)} symbols scored and ready for Agent 3 (Selector).")
