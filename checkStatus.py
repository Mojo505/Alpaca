"""
checkStatus — Periodic Position Monitor
========================================
Run this throughout the trading day to stay on top of your portfolio.
It re-analyzes every open position and pending order with fresh market data,
then recommends and applies TP / SL / Trailing Stop adjustments.

What it does:
  1. Fetches all open positions and pending orders from Alpaca
  2. Calls Agent 2 (Analyst) internally on each symbol — fresh signals every run
  3. For PENDING BUY ORDERS:
       → Checks if entry price is still valid (Fibonacci + Alligator)
       → Flags orders that should be adjusted or cancelled
  4. For OPEN POSITIONS:
       → Calculates Take Profit  (nearest Fibonacci resistance above price)
       → Calculates Stop Loss    (Fibonacci support below entry price)
       → Calculates Trailing Stop (if unrealized P&L > 5%)
       → Checks if TP/SL already exist — won't duplicate
  5. Prints a full status report
  6. Prompts before applying any changes

Run:  python checkStatus.py
Run in loop:  python checkStatus.py --loop 30   (every 30 minutes)
"""

import sys
import time
from datetime import datetime

from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    StopOrderRequest,
    TrailingStopOrderRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

from utils.alpaca_client import trading_client
from agents.analyst import analyze
from utils import telegram_notifier as tg
from utils.report_generator import generate as generate_report


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_check(auto_apply: bool = False, publish_report: bool = False) -> None:
    """
    Main entry point.
    auto_apply=False     → show recommendations and ask before acting
    auto_apply=True      → apply all adjustments without prompting (for scheduled runs)
    publish_report=True  → generate HTML dashboard + send Telegram summary
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_lines = []   # collects output lines for the HTML report

    def log(line: str = "") -> None:
        print(line)
        log_lines.append(line)

    log("\n" + "═" * 65)
    log(f"  CHECK STATUS — {now}")
    log("═" * 65)

    # ── Step 1: Fetch account state ──────────────────────────────────────────
    positions    = _get_positions()
    all_orders   = _get_all_open_orders()
    buy_orders   = [o for o in all_orders if o.side == OrderSide.BUY]
    sell_orders  = [o for o in all_orders if o.side == OrderSide.SELL]

    # Symbols that already have sell-side orders (TP/SL already set)
    covered_sells = {o.symbol for o in sell_orders}

    if not positions and not buy_orders:
        log("\n  Nothing to monitor — no open positions or pending orders.\n")
        if publish_report:
            generate_report(log_lines)
        return

    # ── Step 2: Collect all symbols that need fresh analysis ─────────────────
    position_symbols  = [p.symbol for p in positions]
    buy_order_symbols = list({o.symbol for o in buy_orders})
    all_symbols       = list(set(position_symbols + buy_order_symbols))

    log(f"\n  Symbols to analyze: {', '.join(sorted(all_symbols))}")
    log("  Running fresh analysis — this may take 20-30 seconds...\n")

    scored = analyze(all_symbols)
    scored_by_symbol = {item["symbol"]: item for item in scored}

    # ── Step 3: Review pending BUY orders ───────────────────────────────────
    pending_flags = []
    if buy_orders:
        pending_flags = _review_pending_orders(buy_orders, scored_by_symbol)

    # ── Step 4: Review open positions ───────────────────────────────────────
    position_reports = []
    adjustments      = []
    if positions:
        position_reports, adjustments = _review_positions(
            positions, scored_by_symbol, covered_sells
        )

    # ── Step 5: Print full report ────────────────────────────────────────────
    _print_report(pending_flags, position_reports, sell_orders)

    # ── Step 6: Apply adjustments ────────────────────────────────────────────
    adj_made = 0
    if adjustments:
        adj_made = _apply_adjustments(adjustments, auto_apply)
        for adj in adjustments[:adj_made]:
            tg.notify_adjustment(adj["symbol"], adj["type"],
                                 f"{adj['type'].replace('_',' ').title()} set")
    else:
        log("  No adjustments required — all positions look healthy.\n")

    # ── Step 7: Telegram + HTML report ──────────────────────────────────────
    warnings = sum(1 for r in position_reports if r.get("notes"))
    if publish_report:
        # Send Telegram summary
        tg.notify_check_summary(
            positions=len(positions),
            pending=len(buy_orders),
            adjustments_made=adj_made,
            warnings=warnings,
        )
        # Send warning alerts for any flagged positions
        for r in position_reports:
            for note in r.get("notes", []):
                if "WARNING" in note or "⚠️" in note:
                    tg.notify_warning(r["symbol"], note)

        # Generate and save HTML dashboard
        generate_report(log_lines)

    log("═" * 65 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 helpers — fetch Alpaca state
# ─────────────────────────────────────────────────────────────────────────────

def _get_positions() -> list:
    try:
        return trading_client.get_all_positions()
    except Exception as e:
        print(f"  ERROR fetching positions: {e}")
        return []


def _get_all_open_orders() -> list:
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)
        return trading_client.get_orders(req)
    except Exception as e:
        print(f"  ERROR fetching open orders: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Review pending BUY orders
# ─────────────────────────────────────────────────────────────────────────────

def _review_pending_orders(buy_orders: list, scored_by_symbol: dict) -> list[dict]:
    """
    For each pending buy order:
    - Check Alligator state (has it reversed since we placed the order?)
    - Check Fibonacci entry price (has it drifted > 2%?)
    - Flag anything that needs attention
    """
    flags = []
    for order in buy_orders:
        symbol    = order.symbol
        old_limit = float(order.limit_price) if order.limit_price else None
        notional  = float(order.notional)    if order.notional    else None

        flag = {
            "symbol":    symbol,
            "order_id":  str(order.id),
            "old_limit": old_limit,
            "notional":  notional,
            "issues":    [],
            "action":    "ok",
        }

        if symbol not in scored_by_symbol:
            flag["issues"].append("Symbol no longer in scan universe")
            flag["action"] = "cancel"
            flags.append(flag)
            continue

        signals = scored_by_symbol[symbol]["signals"]
        ali_state   = signals.get("alligator_state", "")
        ali_bearish = signals.get("alligator_bearish", False)
        ali_bullish = signals.get("alligator_bullish", True)

        # Alligator reversed?
        if ali_bearish or "sleeping" in ali_state.lower():
            flag["issues"].append(f"Alligator reversed → {ali_state}")
            flag["action"] = "cancel"

        # Entry price drifted?
        new_entry = signals.get("fib_entry_price")
        if old_limit and new_entry:
            drift = (new_entry - old_limit) / old_limit
            if abs(drift) > 0.02:
                direction = "higher ↑" if drift > 0 else "lower ↓"
                flag["issues"].append(
                    f"Entry drifted {drift*100:+.1f}% {direction} "
                    f"(${old_limit:.2f} → ${new_entry:.2f})"
                )
                flag["new_entry"]   = new_entry
                flag["entry_type"]  = signals.get("fib_entry_type", "limit")
                if flag["action"] != "cancel":
                    flag["action"] = "adjust"

        flag["alligator_state"] = ali_state
        flag["new_entry"]       = flag.get("new_entry", old_limit)
        flags.append(flag)

    return flags


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Review open positions
# ─────────────────────────────────────────────────────────────────────────────

def _review_positions(
    positions: list,
    scored_by_symbol: dict,
    covered_sells: set[str],
) -> tuple[list[dict], list[dict]]:
    """
    For each open position, compute TP / SL / trailing stop levels
    using fresh Fibonacci data from the analyst.
    Returns (reports, adjustments_to_apply).
    """
    reports     = []
    adjustments = []

    for pos in positions:
        symbol      = pos.symbol
        entry       = float(pos.avg_entry_price)
        current     = float(pos.current_price)
        qty         = float(pos.qty)
        unreal_pl   = float(pos.unrealized_pl)
        unreal_pct  = float(pos.unrealized_plpc) * 100
        mkt_val     = float(pos.market_value)

        report = {
            "symbol":     symbol,
            "entry":      entry,
            "current":    current,
            "qty":        qty,
            "unreal_pl":  unreal_pl,
            "unreal_pct": unreal_pct,
            "mkt_val":    mkt_val,
            "tp":         None,
            "sl":         None,
            "trail":      None,
            "alligator":  "No data",
            "score":      "N/A",
            "covered":    symbol in covered_sells,
            "notes":      [],
        }

        if symbol not in scored_by_symbol:
            report["notes"].append("⚠️  No fresh analysis available")
            reports.append(report)
            continue

        item    = scored_by_symbol[symbol]
        signals = item["signals"]
        score   = item["score"]

        report["score"]     = score
        report["alligator"] = signals.get("alligator_state", "Unknown")

        # ── Calculate TP, SL, Trail from Fibonacci ───────────────────────────
        tp, sl, trail = _calculate_levels(entry, current, unreal_pct, signals)
        report["tp"]    = tp
        report["sl"]    = sl
        report["trail"] = trail

        # ── Flag if Alligator turned bearish ─────────────────────────────────
        if signals.get("alligator_bearish") or "sleeping" in report["alligator"].lower():
            report["notes"].append(
                f"⚠️  Alligator turned → {report['alligator']} — consider closing position"
            )

        # ── Flag if score dropped significantly ──────────────────────────────
        if score <= 4:
            report["notes"].append(f"⚠️  Score dropped to {score}/10 — setup weakening")

        # ── Queue adjustments if TP/SL not yet set ───────────────────────────
        if not symbol in covered_sells:
            if tp:
                adjustments.append({
                    "type":   "take_profit",
                    "symbol": symbol,
                    "qty":    qty,
                    "price":  tp,
                })
            if sl:
                adjustments.append({
                    "type":   "stop_loss",
                    "symbol": symbol,
                    "qty":    qty,
                    "price":  sl,
                })
        else:
            report["notes"].append("ℹ️  TP/SL orders already exist on this position")

        # Trailing stop is independent — add if not already trailing
        if trail and symbol not in covered_sells:
            adjustments.append({
                "type":          "trailing_stop",
                "symbol":        symbol,
                "qty":           qty,
                "trail_percent": trail,
            })

        reports.append(report)

    return reports, adjustments


def _calculate_levels(
    entry: float,
    current: float,
    unreal_pct: float,
    signals: dict,
) -> tuple[float | None, float | None, float | None]:
    """
    Determine TP, SL, and trailing stop percent using Fibonacci levels.

    Take Profit  → nearest Fibonacci level ABOVE current price
                   fallback: swing high (maximum upside target)

    Stop Loss    → nearest Fibonacci level BELOW entry price
                   fallback: entry × 0.97 (3% hard stop)

    Trailing Stop→ only activated when unrealized P&L > 5%
                   uses 3% trail so gains are locked in if price reverses
    """
    fib_levels  = signals.get("fib_levels", {})
    swing_high  = signals.get("fib_swing_high")
    swing_low   = signals.get("fib_swing_low")

    level_prices = sorted(fib_levels.values())   # ascending price order

    # ── Take Profit: lowest Fibonacci level ABOVE current price ──────────────
    tp = None
    candidates_above = [p for p in level_prices if p > current * 1.005]
    if candidates_above:
        tp = round(min(candidates_above), 2)
    elif swing_high and swing_high > current:
        tp = round(swing_high, 2)   # fallback: swing high

    # ── Stop Loss: highest Fibonacci level BELOW entry price ─────────────────
    sl = None
    candidates_below = [p for p in level_prices if p < entry * 0.995]
    if candidates_below:
        sl = round(max(candidates_below), 2)
    else:
        sl = round(entry * 0.97, 2)   # fallback: 3% hard stop

    # ── Trailing Stop: activate only once P&L > +5% ──────────────────────────
    trail = None
    if unreal_pct >= 5.0:
        trail = 3.0   # 3% trailing stop once we're 5% in profit

    return tp, sl, trail


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Print full report
# ─────────────────────────────────────────────────────────────────────────────

def _print_report(
    pending_flags: list[dict],
    position_reports: list[dict],
    existing_sell_orders: list,
) -> None:

    # ── Pending orders section ────────────────────────────────────────────────
    print("\n  ── PENDING BUY ORDERS ────────────────────────────────────")
    if not pending_flags:
        print("  None.")
    else:
        for f in pending_flags:
            status_icon = {"ok": "✅", "adjust": "⚠️ ", "cancel": "❌"}.get(f["action"], "?")
            limit_str   = f"Limit @ ${f['old_limit']:.2f}" if f["old_limit"] else "Market"
            print(f"\n  {status_icon} {f['symbol']:<6}  {limit_str}")
            print(f"     Alligator: {f.get('alligator_state', 'N/A')}")
            for issue in f.get("issues", []):
                print(f"     ⚠️  {issue}")
            if f["action"] == "adjust":
                print(f"     → Recommend adjusting entry to ${f.get('new_entry', 'N/A'):.2f}")
            elif f["action"] == "cancel":
                print(f"     → Recommend cancelling this order")

    # ── Existing sell orders (TP/SL) ─────────────────────────────────────────
    if existing_sell_orders:
        print(f"\n  ── ACTIVE TP / SL / TRAIL ORDERS ────────────────────────")
        for o in existing_sell_orders:
            order_type = (
                f"Limit (TP) @ ${float(o.limit_price):.2f}"  if o.limit_price and not o.stop_price else
                f"Stop  (SL) @ ${float(o.stop_price):.2f}"   if o.stop_price  else
                f"Trailing stop {o.trail_percent}%"           if o.trail_percent else
                o.type.value
            )
            print(f"  ↔  {o.symbol:<6}  SELL  {order_type}")

    # ── Position section ──────────────────────────────────────────────────────
    print(f"\n  ── OPEN POSITIONS ────────────────────────────────────────")
    if not position_reports:
        print("  None.")
    else:
        for r in position_reports:
            pl_icon = "▲" if r["unreal_pl"] >= 0 else "▼"
            covered = "  [TP/SL SET]" if r["covered"] else "  [NO TP/SL]"
            print(f"""
  {'─'*58}
  {r['symbol']:<6}  Score: {r['score']}/10{covered}
    Entry:     ${r['entry']:.2f}   →   Current: ${r['current']:.2f}
    P&L:       {pl_icon} ${abs(r['unreal_pl']):.2f}  ({r['unreal_pct']:+.2f}%)
    Alligator: {r['alligator']}

    Recommended levels:
      Take Profit  (TP): {"$" + str(r['tp'])   if r['tp']    else "None (at swing high)"}
      Stop Loss    (SL): {"$" + str(r['sl'])   if r['sl']    else "None"}
      Trailing Stop    : {str(r['trail']) + "% trail (P&L > 5%)" if r['trail'] else "Not yet (P&L < 5% threshold)"}""")

            for note in r.get("notes", []):
                print(f"    {note}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 — Apply adjustments
# ─────────────────────────────────────────────────────────────────────────────

def _apply_adjustments(adjustments: list[dict], auto_apply: bool) -> int:
    """
    Show planned adjustments and apply them — either automatically
    or after user confirmation.
    """
    print(f"\n  ── PLANNED ADJUSTMENTS ({len(adjustments)}) ─────────────────────────")
    for adj in adjustments:
        if adj["type"] == "take_profit":
            print(f"  + {adj['symbol']:<6} SET TAKE PROFIT  @ ${adj['price']:.2f}  "
                  f"(sell {adj['qty']:.4f} shares)")
        elif adj["type"] == "stop_loss":
            print(f"  + {adj['symbol']:<6} SET STOP LOSS    @ ${adj['price']:.2f}  "
                  f"(sell {adj['qty']:.4f} shares)")
        elif adj["type"] == "trailing_stop":
            print(f"  + {adj['symbol']:<6} SET TRAILING STOP  {adj['trail_percent']}%  "
                  f"(sell {adj['qty']:.4f} shares when reversed)")

    if not auto_apply:
        print()
        answer = input("  Apply all adjustments? (y / n): ").strip().lower()
        if answer != "y":
            print("  Adjustments skipped.\n")
            return 0

    print()
    count = 0
    for adj in adjustments:
        _execute_adjustment(adj)
        count += 1
    return count


def _execute_adjustment(adj: dict) -> None:
    """Place a single TP, SL, or trailing stop order on Alpaca."""
    symbol = adj["symbol"]
    qty    = round(adj["qty"], 8)

    try:
        if adj["type"] == "take_profit":
            req = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                limit_price=adj["price"],
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,   # Good Till Cancelled
            )
            order = trading_client.submit_order(req)
            print(f"  ✅ {symbol:<6} TP set  @ ${adj['price']:.2f}  "
                  f"order_id={str(order.id)[:8]}...")

        elif adj["type"] == "stop_loss":
            req = StopOrderRequest(
                symbol=symbol,
                qty=qty,
                stop_price=adj["price"],
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
            )
            order = trading_client.submit_order(req)
            print(f"  ✅ {symbol:<6} SL set  @ ${adj['price']:.2f}  "
                  f"order_id={str(order.id)[:8]}...")

        elif adj["type"] == "trailing_stop":
            req = TrailingStopOrderRequest(
                symbol=symbol,
                qty=qty,
                trail_percent=adj["trail_percent"],
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
            )
            order = trading_client.submit_order(req)
            print(f"  ✅ {symbol:<6} Trail   {adj['trail_percent']}%  "
                  f"order_id={str(order.id)[:8]}...")

    except Exception as e:
        print(f"  ❌ {symbol:<6} FAILED: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    auto   = "--auto"   in sys.argv
    report = "--report" in sys.argv

    if "--loop" in sys.argv:
        try:
            minutes = int(sys.argv[sys.argv.index("--loop") + 1])
        except (IndexError, ValueError):
            minutes = 30
        interval = minutes * 60

        print(f"checkStatus running every {minutes} min. Press Ctrl+C to stop.")
        while True:
            run_check(auto_apply=auto, publish_report=report)
            print(f"  Next check in {minutes} minute(s)...\n")
            time.sleep(interval)
    else:
        run_check(auto_apply=auto, publish_report=report)
