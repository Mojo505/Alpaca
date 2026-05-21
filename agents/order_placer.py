"""
Agent 4 — Order Placer
======================
WHAT IT DOES:
  Takes Claude's 4 picks and turns them into real Alpaca orders.

  Before placing any order it runs 5 safety checks:
    1. Is the market currently open?
    2. Is buying power sufficient?
    3. Do we already hold a POSITION in this stock?   (avoid doubling up)
    4. Do we already have an OPEN ORDER for this stock? (pending limit orders)
    5. Is the dollar amount above Alpaca's minimum ($1)?

  Checks 3 and 4 are both required:
    - A position = shares already owned and settled
    - An open order = a pending buy that hasn't filled yet (e.g. a limit order
      placed earlier today that's still waiting for price to drop)
  Both block a new order to prevent accidental double-exposure.

  Uses FRACTIONAL / NOTIONAL orders — you specify a dollar amount,
  not a number of shares. This means you can invest exactly $250 in
  TSLA without worrying about share price.

OUTPUT:
  List of order results:
  {
    "symbol":    "TSLA",
    "status":    "submitted" | "skipped" | "failed",
    "order_id":  "abc-123",       ← Alpaca order UUID
    "notional":  250.00,          ← dollars invested
    "reason":    "..."            ← filled if skipped/failed
  }

HOW TO RUN STANDALONE:
  python agents/order_placer.py

  NOTE: This runs against your PAPER trading account (safe).
  To switch to live trading, change paper=True → False in alpaca_client.py
"""

from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from utils.alpaca_client import trading_client
import config


# ── Public entry point ───────────────────────────────────────────────────────

def place_orders(picks: list[dict], total_budget: float = None) -> list[dict]:
    """
    Main entry point. Runs pre-flight checks then submits orders.
    Called by the orchestrator after the user has confirmed the investment amount.

    total_budget: exact dollar amount to invest across all picks.
                  If None, falls back to buying_power × POSITION_BUDGET (config default).
    """
    print("[OrderPlacer] Running pre-flight checks...")

    # Check 1: Is the market open?
    if not _market_is_open():
        print("[OrderPlacer] Market is CLOSED. Orders will be queued for next open.")

    # Check 2: Get account buying power
    account       = trading_client.get_account()
    buying_power  = float(account.buying_power)
    portfolio_val = float(account.portfolio_value)
    print(f"[OrderPlacer] Account: ${buying_power:,.2f} buying power | "
          f"${portfolio_val:,.2f} portfolio value")

    if buying_power < 10:
        print("[OrderPlacer] ERROR: Insufficient buying power (< $10). Cannot place orders.")
        return []

    # Resolve the budget to use
    if total_budget is None:
        total_budget = round(buying_power * config.POSITION_BUDGET, 2)
        print(f"[OrderPlacer] Using default budget: ${total_budget:,.2f} "
              f"({config.POSITION_BUDGET*100:.0f}% of buying power)")
    else:
        if total_budget > buying_power:
            print(f"[OrderPlacer] WARNING: Requested ${total_budget:,.2f} exceeds "
                  f"buying power ${buying_power:,.2f}. Capping to buying power.")
            total_budget = buying_power
        print(f"[OrderPlacer] Using requested budget: ${total_budget:,.2f}")

    # Check 3 + 4: What do we already hold or have pending orders for?
    existing_positions, open_orders = _get_active_symbols()
    blocked = existing_positions | open_orders

    _print_active_trades(existing_positions, open_orders)

    # Place each order
    results = []
    for pick in picks:
        result = _place_single_order(
            pick, total_budget, blocked, existing_positions, open_orders
        )
        results.append(result)

    # Summary
    submitted = [r for r in results if r["status"] == "submitted"]
    print(f"\n[OrderPlacer] Done. {len(submitted)}/{len(picks)} orders submitted.")
    return results


# ── Pre-flight helpers ───────────────────────────────────────────────────────

def _market_is_open() -> bool:
    """
    Check Alpaca's clock endpoint to see if the US market is currently open.
    Market hours: Mon–Fri, 9:30am – 4:00pm ET (excluding holidays).
    """
    clock = trading_client.get_clock()
    return clock.is_open


def _get_active_symbols() -> tuple[set[str], set[str]]:
    """
    Query Alpaca for two things:
      1. Open POSITIONS  — symbols where we already own shares
      2. Open ORDERS     — symbols with a pending buy order not yet filled

    Returns: (position_symbols, open_order_symbols)

    Why check both?
      Positions:   You already own TSLA → buying more doubles your exposure.
      Open orders: You placed a TSLA limit order this morning at $280.
                   It hasn't filled yet, but it's live.
                   Placing a second order now means if price hits $280,
                   BOTH orders fill and you're suddenly 2× exposed.

    We block both — the caller combines them into a single 'blocked' set.
    """
    position_symbols   = set()
    open_order_symbols = set()

    # ── Fetch open positions ──────────────────────────────────────────────────
    try:
        positions = trading_client.get_all_positions()
        position_symbols = {p.symbol for p in positions}
    except Exception as e:
        print(f"[OrderPlacer] Warning: could not fetch positions — {e}")

    # ── Fetch open orders (pending, not yet filled) ───────────────────────────
    try:
        request = GetOrdersRequest(
            status=QueryOrderStatus.OPEN,   # only OPEN (pending) orders
            side=OrderSide.BUY,             # only buy orders (we don't short)
            limit=100,                      # max orders to fetch in one call
        )
        open_orders = trading_client.get_orders(request)
        open_order_symbols = {o.symbol for o in open_orders}
    except Exception as e:
        print(f"[OrderPlacer] Warning: could not fetch open orders — {e}")

    return position_symbols, open_order_symbols


def _print_active_trades(positions: set[str], open_orders: set[str]) -> None:
    """
    Print a clear breakdown of what's already active on the account.
    This runs before any order is placed so the user can see what will be skipped.
    """
    print("\n[OrderPlacer] ── Active Alpaca Account State ─────────────────")

    if positions:
        print(f"[OrderPlacer]   Open positions  ({len(positions)}): "
              f"{', '.join(sorted(positions))}")
    else:
        print("[OrderPlacer]   Open positions:   none")

    if open_orders:
        print(f"[OrderPlacer]   Pending orders  ({len(open_orders)}): "
              f"{', '.join(sorted(open_orders))}")
    else:
        print("[OrderPlacer]   Pending orders:   none")

    blocked = positions | open_orders
    if blocked:
        print(f"[OrderPlacer]   Will SKIP these: {', '.join(sorted(blocked))}")
    else:
        print("[OrderPlacer]   Nothing to skip — all 4 picks are clear to trade")
    print("[OrderPlacer] ─────────────────────────────────────────────────\n")


# ── Order execution ──────────────────────────────────────────────────────────

def _place_single_order(
    pick: dict,
    total_budget: float,
    blocked: set[str],
    existing_positions: set[str],
    open_orders: set[str],
) -> dict:
    """
    Place one order for a single pick, after verifying it's not blocked.

    total_budget:  the total dollar amount to invest across ALL picks.
                   Each pick gets its share based on position_weight.
    Order type:    LIMIT  → Fibonacci entry price available
                   MARKET → fallback, fills immediately
    Time in force: DAY    → auto-cancels at market close if unfilled
    """
    symbol        = pick["symbol"]
    weight        = pick["position_weight"]
    dollar_amount = round(total_budget * weight, 2)   # simple weight split

    # Safety check: position or open order already exists?
    if symbol in existing_positions:
        return {
            "symbol":   symbol,
            "status":   "skipped",
            "notional": 0,
            "reason":   f"Already holding an open POSITION in {symbol}",
        }
    if symbol in open_orders:
        return {
            "symbol":   symbol,
            "status":   "skipped",
            "notional": 0,
            "reason":   f"Already have a pending OPEN ORDER for {symbol} — waiting to fill",
        }

    # Safety check: minimum order size
    if dollar_amount < 1.0:
        return {
            "symbol":  symbol,
            "status":  "skipped",
            "notional": dollar_amount,
            "reason":  f"Dollar amount ${dollar_amount:.2f} is below $1 minimum",
        }

    # Build the order — use limit if Agent 2 found a Fibonacci entry price,
    # otherwise fall back to a market order
    entry_type  = pick.get("entry_type", "market")
    entry_price = pick.get("entry_price")

    if entry_type == "limit" and entry_price:
        order_request = LimitOrderRequest(
            symbol=symbol,
            notional=dollar_amount,
            limit_price=round(float(entry_price), 2),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,   # cancels at close if not filled
        )
        order_label = f"LIMIT @ ${entry_price}"
    else:
        order_request = MarketOrderRequest(
            symbol=symbol,
            notional=dollar_amount,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        order_label = "MARKET"

    try:
        order = trading_client.submit_order(order_request)
        print(f"[OrderPlacer] ✓ {symbol:<6} ${dollar_amount:>8.2f}  "
              f"{order_label}  order_id={str(order.id)[:8]}...")
        return {
            "symbol":      symbol,
            "status":      "submitted",
            "order_id":    str(order.id),
            "notional":    dollar_amount,
            "entry_type":  order_label,
            "reasoning":   pick.get("reasoning", ""),
            "risk_note":   pick.get("risk_note", ""),
        }

    except Exception as e:
        print(f"[OrderPlacer] ✗ {symbol:<6} FAILED — {e}")
        return {
            "symbol":  symbol,
            "status":  "failed",
            "notional": dollar_amount,
            "reason":  str(e),
        }


# ── Entry point adjustment ───────────────────────────────────────────────────

def adjust_open_orders(scored_by_symbol: dict) -> list[dict]:
    """
    Review all open (unfilled) BUY orders and adjust if the entry is stale.
    Called by the orchestrator BEFORE placing new orders.

    scored_by_symbol: dict keyed by symbol → { score, signals }
                      Built from the analyst's output in the orchestrator.

    Adjustment rules (applied in priority order):
      1. CANCEL  if symbol no longer passes our scan filters (not in analysis)
      2. CANCEL  if Alligator turned bearish or sleeping (setup invalidated)
      3. ADJUST  if Fibonacci entry price shifted by > 2%  (better level found)
      4. LEAVE   if entry price is still valid (do nothing)

    Returns list of { symbol, action, reason, old_entry, new_entry, order_id }
    """
    print("\n[OrderPlacer] ── Reviewing open orders for stale entry points ──")

    try:
        request     = GetOrdersRequest(status=QueryOrderStatus.OPEN,
                                       side=OrderSide.BUY, limit=100)
        open_orders = trading_client.get_orders(request)
    except Exception as e:
        print(f"[OrderPlacer] Could not fetch open orders: {e}")
        return []

    if not open_orders:
        print("[OrderPlacer] No open orders found — nothing to adjust.")
        return []

    print(f"[OrderPlacer] Found {len(open_orders)} open order(s) to review.")
    results = []

    for order in open_orders:
        symbol    = order.symbol
        order_id  = str(order.id)
        old_limit = float(order.limit_price) if order.limit_price else None
        notional  = float(order.notional)    if order.notional    else None

        print(f"\n[OrderPlacer]   Reviewing {symbol} — "
              f"{'Limit @ $' + str(round(old_limit,2)) if old_limit else 'Market'}")

        # ── Rule 1: Symbol no longer in our analysed universe ────────────────
        if symbol not in scored_by_symbol:
            _cancel_order(order_id, symbol)
            results.append({
                "symbol":    symbol,
                "action":    "cancelled",
                "reason":    "Symbol no longer passes scan/volume filters",
                "old_entry": old_limit,
                "new_entry": None,
            })
            continue

        signals = scored_by_symbol[symbol]["signals"]

        # ── Rule 2: Alligator turned bearish or sleeping ──────────────────────
        ali_state   = signals.get("alligator_state", "")
        ali_bullish = signals.get("alligator_bullish", True)
        ali_bearish = signals.get("alligator_bearish", False)

        if ali_bearish or "sleeping" in ali_state.lower():
            _cancel_order(order_id, symbol)
            results.append({
                "symbol":    symbol,
                "action":    "cancelled",
                "reason":    f"Alligator state changed → {ali_state}",
                "old_entry": old_limit,
                "new_entry": None,
            })
            continue

        # ── Rule 3: Fibonacci entry price shifted > 2% ────────────────────────
        new_entry_price = signals.get("fib_entry_price")
        new_entry_type  = signals.get("fib_entry_type", "market")

        if new_entry_price and old_limit:
            shift_pct = (new_entry_price - old_limit) / old_limit   # signed

            if abs(shift_pct) > 0.02:   # more than 2% drift
                direction = "↑ higher" if shift_pct > 0 else "↓ lower"
                print(f"[OrderPlacer]   Entry shifted {shift_pct*100:+.1f}% {direction} "
                      f"(${old_limit:.2f} → ${new_entry_price:.2f}). Adjusting...")

                _cancel_order(order_id, symbol)
                new_order = _submit_adjusted_order(
                    symbol, notional, new_entry_price, new_entry_type
                )
                results.append({
                    "symbol":     symbol,
                    "action":     "adjusted",
                    "reason":     f"Fibonacci level shifted {shift_pct*100:+.1f}%",
                    "old_entry":  old_limit,
                    "new_entry":  new_entry_price,
                    "order_id":   new_order.get("order_id"),
                })
                continue

        # ── Rule 4: Entry still valid — leave it ─────────────────────────────
        print(f"[OrderPlacer]   ✓ Entry price still valid — leaving order unchanged.")
        results.append({
            "symbol":    symbol,
            "action":    "unchanged",
            "reason":    "Entry price within 2% tolerance",
            "old_entry": old_limit,
            "new_entry": old_limit,
        })

    # Print summary
    _print_adjustment_summary(results)
    return results


def _cancel_order(order_id: str, symbol: str) -> None:
    """Cancel a single order by ID. Logs the outcome."""
    try:
        trading_client.cancel_order_by_id(order_id)
        print(f"[OrderPlacer]   ✗ Cancelled {symbol} order {order_id[:8]}...")
    except Exception as e:
        print(f"[OrderPlacer]   ERROR cancelling {symbol}: {e}")


def _submit_adjusted_order(
    symbol: str,
    notional: float,
    entry_price: float,
    entry_type: str,
) -> dict:
    """Place a replacement order at the updated entry price."""
    try:
        if entry_type == "limit" and entry_price:
            req = LimitOrderRequest(
                symbol=symbol,
                notional=round(notional, 2),
                limit_price=round(entry_price, 2),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            label = f"LIMIT @ ${entry_price:.2f}"
        else:
            req = MarketOrderRequest(
                symbol=symbol,
                notional=round(notional, 2),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            label = "MARKET"

        order = trading_client.submit_order(req)
        print(f"[OrderPlacer]   ✓ Replaced with {label}  "
              f"order_id={str(order.id)[:8]}...")
        return {"order_id": str(order.id), "label": label}

    except Exception as e:
        print(f"[OrderPlacer]   ERROR placing adjusted order for {symbol}: {e}")
        return {"order_id": None, "label": "failed"}


def _print_adjustment_summary(results: list[dict]) -> None:
    counts = {"adjusted": 0, "cancelled": 0, "unchanged": 0}
    for r in results:
        counts[r["action"]] = counts.get(r["action"], 0) + 1

    print(f"\n[OrderPlacer] Adjustment summary → "
          f"adjusted: {counts['adjusted']}  |  "
          f"cancelled: {counts['cancelled']}  |  "
          f"unchanged: {counts['unchanged']}")
    print("[OrderPlacer] ─────────────────────────────────────────────────\n")


# ── Pretty printer ───────────────────────────────────────────────────────────

def _print_results(results: list[dict]) -> None:
    print("\n" + "═" * 60)
    print("  ORDER PLACER RESULTS")
    print("═" * 60)

    for r in results:
        icon = {"submitted": "✅", "skipped": "⏭️", "failed": "❌"}.get(r["status"], "?")
        print(f"\n  {icon} {r['symbol']:<6}  ${r.get('notional', 0):>8.2f}  [{r['status'].upper()}]")
        if r["status"] == "submitted":
            print(f"     Order ID:   {r.get('order_id', 'N/A')}")
            print(f"     Entry type: {r.get('entry_type', 'N/A')}")
        elif r["status"] in ("skipped", "failed"):
            print(f"     Reason:     {r.get('reason', 'N/A')}")

    submitted_total = sum(r["notional"] for r in results if r["status"] == "submitted")
    skipped = [r for r in results if r["status"] == "skipped"]
    print(f"\n  Orders submitted: {len([r for r in results if r['status'] == 'submitted'])}")
    print(f"  Orders skipped:   {len(skipped)}")
    print(f"  Total deployed:   ${submitted_total:,.2f}")
    print("═" * 60 + "\n")


# ── Standalone runner ────────────────────────────────────────────────────────

if __name__ == "__main__":
    from agents.scanner import scan_market
    from agents.analyst import analyze
    from agents.selector import select_stocks

    print("Testing Agent 4 — Order Placer (PAPER trading)\n")

    symbols = scan_market()
    scored  = analyze(symbols)
    picks   = select_stocks(scored)

    print(f"\nPlacing orders for: {[p['symbol'] for p in picks]}\n")
    results = place_orders(picks)
    _print_results(results)
