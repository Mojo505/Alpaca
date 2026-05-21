"""
Quick test for Agent 3 — Selector.
Runs scanner → analyst → selector and prints Claude's final picks.

Run: python test_selector.py
"""
from agents.scanner import scan_market
from agents.analyst import analyze
from agents.selector import select_stocks, _print_results

if __name__ == "__main__":
    print("=" * 60)
    print("  Testing Agent 3 — Selector (Claude LLM)")
    print("=" * 60 + "\n")

    symbols = scan_market()
    scored  = analyze(symbols)
    picks   = select_stocks(scored)

    if not picks:
        print("ERROR: No picks returned. Check your ANTHROPIC_API_KEY in .env")
        exit(1)

    _print_results(picks)
    print(f"SUCCESS — {len(picks)} picks ready for Agent 4 (Order Placer).")
