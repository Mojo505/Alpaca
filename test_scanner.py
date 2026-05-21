"""
Quick test for Agent 1 — runs the scanner and prints results.
Use this to verify your API keys work before building the other agents.

Run: python test_scanner.py
"""
from agents.scanner import scan_market

if __name__ == "__main__":
    print("Testing Agent 1 — Market Scanner\n")

    symbols = scan_market()

    if not symbols:
        print("ERROR: No symbols returned. Check your .env keys and internet connection.")
    else:
        print(f"\nSUCCESS — {len(symbols)} symbols ready for the analyst:")
        print(", ".join(symbols[:10]), "..." if len(symbols) > 10 else "")
