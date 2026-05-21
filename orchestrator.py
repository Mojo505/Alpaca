"""
Orchestrator — runs all 4 agents in sequence.
=============================================
This is the file you run each trading day.

Pipeline:
  Agent 1 (Scanner)      → finds liquid, tradeable stocks
  Agent 2 (Analyst)      → scores each with RSI, EMA, MA, Fibonacci, Alligator
  Agent 2b (Adjustment)  → reviews open orders, cancels/re-enters stale positions
  Agent 3 (Selector)     → Claude picks the best 4 NEW trades
  Agent 4 (OrderPlacer)  → submits paper trades to Alpaca

Run:  python orchestrator.py
"""

from datetime import datetime

from agents.scanner      import scan_market
from agents.analyst      import analyze
from agents.selector     import select_stocks
from agents.order_placer import place_orders, adjust_open_orders
from utils.alpaca_client import trading_client


def run(forced_budget: float = None, publish_report: bool = False):
    start = datetime.now()
    print("=" * 60)
    print(f"  TRADING PIPELINE — {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60 + "\n")

    # ── Agent 1: Scan ────────────────────────────────────────────
    print("── AGENT 1: Market Scanner ──────────────────────────────")
    symbols = scan_market()
    if not symbols:
        print("Pipeline stopped: Scanner returned no symbols.")
        return
    print(f"→ {len(symbols)} symbols ready for analysis\n")

    # ── Agent 2: Analyse ─────────────────────────────────────────
    print("── AGENT 2: Analyst ─────────────────────────────────────")
    scored = analyze(symbols)
    if not scored:
        print("Pipeline stopped: Analyst returned no results.")
        return
    print(f"→ Top pick: {scored[0]['symbol']} (score {scored[0]['score']}/10)\n")

    # Convert list → dict for O(1) lookups in the adjustment step
    scored_by_symbol = {item["symbol"]: item for item in scored}

    # ── Agent 2b: Adjust stale open orders ───────────────────────
    # Run BEFORE selecting new trades — so stale orders are cleaned up
    # and freed-up symbols can be re-considered by the selector.
    print("── AGENT 2b: Open Order Adjustment ─────────────────────")
    adjust_open_orders(scored_by_symbol)

    # ── Agent 3: Select ──────────────────────────────────────────
    print("── AGENT 3: Selector (Claude LLM) ───────────────────────")
    picks = select_stocks(scored)
    if not picks:
        print("Pipeline stopped: Selector returned no picks.")
        return
    print(f"→ Claude selected: {[p['symbol'] for p in picks]}\n")

    # ── Investment amount prompt ──────────────────────────────────
    # Skip interactive prompt if a budget was passed directly (CI/automated mode)
    if forced_budget is not None:
        total_budget = forced_budget
        print(f"  [Auto] Using forced budget: ${total_budget:,.2f}\n")
    else:
        total_budget = _ask_investment_amount(picks)
        if total_budget is None:
            print("\nPipeline cancelled by user.")
            return

    # ── Agent 4: Place new orders ─────────────────────────────────
    print("── AGENT 4: Order Placer ────────────────────────────────")
    results = place_orders(picks, total_budget=total_budget)

    # ── Final summary ─────────────────────────────────────────────
    elapsed   = (datetime.now() - start).seconds
    submitted = [r for r in results if r["status"] == "submitted"]
    skipped   = [r for r in results if r["status"] == "skipped"]
    total_deployed = sum(r["notional"] for r in submitted)

    print("\n" + "=" * 60)
    print("  PIPELINE COMPLETE")
    print("=" * 60)
    print(f"  Runtime:          {elapsed}s")
    print(f"  Orders submitted: {len(submitted)} / {len(picks)}")
    print(f"  Orders skipped:   {len(skipped)} (duplicate / already active)")
    print(f"  Capital deployed: ${total_deployed:,.2f}")
    print()
    for r in results:
        icon = {"submitted": "✅", "skipped": "⏭️", "failed": "❌"}.get(r["status"], "?")
        print(f"  {icon}  {r['symbol']:<6} ${r.get('notional', 0):>8.2f}  [{r['status']}]")
    print("=" * 60 + "\n")


def _ask_investment_amount(picks: list[dict]) -> float | None:
    """
    Interactive prompt shown after Claude selects the 4 picks.
    Displays the allocation breakdown at every budget the user considers,
    then asks for confirmation before handing control to Agent 4.

    Returns the confirmed dollar amount, or None if the user cancels.
    """
    account       = trading_client.get_account()
    buying_power  = float(account.buying_power)
    default_budget = round(buying_power * 0.20, 2)   # 20% default

    print("\n" + "─" * 60)
    print("  INVESTMENT AMOUNT")
    print("─" * 60)
    print(f"  Available buying power : ${buying_power:>12,.2f}")
    print(f"  Suggested (20%)        : ${default_budget:>12,.2f}")
    print()
    print("  Claude's picks and weights:")
    for p in picks:
        print(f"    {p['symbol']:<6}  {p['position_weight']*100:.0f}%")
    print()

    while True:
        raw = input(
            "  How much to invest in total?\n"
            "  (enter a dollar amount e.g. 500, or press Enter for "
            f"${default_budget:,.0f}): $"
        ).strip()

        # Use default if user just pressed Enter
        if raw == "":
            total_budget = default_budget
        else:
            try:
                total_budget = float(raw.replace(",", "").replace("$", ""))
                if total_budget <= 0:
                    print("  Please enter a positive amount.\n")
                    continue
            except ValueError:
                print("  Invalid input — enter a number like 500 or 1000.\n")
                continue

        # Guard: can't invest more than available
        if total_budget > buying_power:
            print(f"\n  ⚠️  ${total_budget:,.2f} exceeds your buying power "
                  f"(${buying_power:,.2f}).")
            print(f"  Maximum you can invest: ${buying_power:,.2f}\n")
            continue

        # Show the full allocation breakdown before confirming
        print(f"\n  Allocation breakdown for ${total_budget:,.2f}:")
        print("  " + "─" * 38)
        for p in picks:
            amt = total_budget * p["position_weight"]
            entry = p.get("entry_price")
            entry_str = f"  (limit @ ${entry:.2f})" if entry else "  (market)"
            print(f"    {p['symbol']:<6}  {p['position_weight']*100:>4.0f}%  →  "
                  f"${amt:>9,.2f}{entry_str}")
        print("  " + "─" * 38)
        print(f"    {'TOTAL':<12}  →  ${total_budget:>9,.2f}")
        print()

        confirm = input("  Confirm and place orders? (y / n): ").strip().lower()
        if confirm == "y":
            print()
            return total_budget
        elif confirm == "n":
            retry = input("  Enter a different amount? (y / n): ").strip().lower()
            if retry == "y":
                print()
                continue
            else:
                return None   # user cancelled entirely
        else:
            print("  Please type y or n.\n")


if __name__ == "__main__":
    import sys
    # Non-interactive mode for GitHub Actions:
    #   python orchestrator.py --amount 1000
    #   python orchestrator.py --amount 1000 --report
    if "--amount" in sys.argv:
        try:
            idx    = sys.argv.index("--amount")
            amount = float(sys.argv[idx + 1])
            report = "--report" in sys.argv
            run(forced_budget=amount, publish_report=report)
        except (IndexError, ValueError):
            print("Usage: python orchestrator.py --amount 1000")
    else:
        run()
