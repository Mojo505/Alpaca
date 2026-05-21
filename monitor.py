"""
Monitor — Live Account Dashboard
=================================
Shows a full snapshot of your Alpaca paper account:
  1. Account summary     (portfolio value, buying power, daily P&L)
  2. Open positions      (what you own, unrealized P&L per position)
  3. Pending orders      (limit orders waiting to fill)
  4. Filled today        (orders that executed since market open)

Run any time:  python monitor.py
Run in a loop: python monitor.py --loop 60   (refresh every 60 seconds)
"""

import sys
import time
from datetime import datetime, timezone

from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus, OrderSide
from utils.alpaca_client import trading_client


def show_dashboard() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "═" * 65)
    print(f"  ALPACA ACCOUNT MONITOR  —  {now}")
    print("═" * 65)

    _show_account_summary()
    _show_positions()
    _show_open_orders()
    _show_filled_today()

    print("═" * 65 + "\n")


# ── Section 1: Account summary ───────────────────────────────────────────────

def _show_account_summary() -> None:
    try:
        acct = trading_client.get_account()
        portfolio  = float(acct.portfolio_value)
        buying_pwr = float(acct.buying_power)
        cash       = float(acct.cash)
        equity     = float(acct.equity)

        # Day P&L = equity today vs last close
        last_equity    = float(acct.last_equity)
        day_pl         = equity - last_equity
        day_pl_pct     = (day_pl / last_equity * 100) if last_equity else 0
        day_pl_icon    = "▲" if day_pl >= 0 else "▼"

        print(f"""
  ── Account Summary ───────────────────────────────────────
  Portfolio value : ${portfolio:>12,.2f}
  Buying power    : ${buying_pwr:>12,.2f}
  Cash            : ${cash:>12,.2f}
  Day P&L         : {day_pl_icon} ${abs(day_pl):>10,.2f}  ({day_pl_pct:+.2f}%)""")

        clock = trading_client.get_clock()
        status = "OPEN ✅" if clock.is_open else "CLOSED ❌"
        next_event = clock.next_open if not clock.is_open else clock.next_close
        print(f"  Market status   : {status}  (next event: {next_event})")

    except Exception as e:
        print(f"  ERROR fetching account: {e}")


# ── Section 2: Open positions ────────────────────────────────────────────────

def _show_positions() -> None:
    print(f"\n  ── Open Positions ────────────────────────────────────────")
    try:
        positions = trading_client.get_all_positions()

        if not positions:
            print("  (none)")
            return

        total_pl = 0
        header = f"  {'Symbol':<8} {'Qty':>8} {'Entry':>10} {'Current':>10} {'Mkt Value':>11} {'Unreal P&L':>12} {'%':>7}"
        print(header)
        print("  " + "─" * 63)

        for p in sorted(positions, key=lambda x: x.symbol):
            qty        = float(p.qty)
            entry      = float(p.avg_entry_price)
            current    = float(p.current_price)
            mkt_val    = float(p.market_value)
            unreal_pl  = float(p.unrealized_pl)
            unreal_pct = float(p.unrealized_plpc) * 100
            icon       = "▲" if unreal_pl >= 0 else "▼"
            total_pl  += unreal_pl

            print(f"  {p.symbol:<8} {qty:>8.4f} {entry:>10.2f} {current:>10.2f} "
                  f"{mkt_val:>11,.2f} {icon} ${abs(unreal_pl):>9,.2f} {unreal_pct:>+6.2f}%")

        print("  " + "─" * 63)
        total_icon = "▲" if total_pl >= 0 else "▼"
        print(f"  {'TOTAL UNREALIZED P&L':<43} {total_icon} ${abs(total_pl):>9,.2f}")

    except Exception as e:
        print(f"  ERROR fetching positions: {e}")


# ── Section 3: Pending open orders ───────────────────────────────────────────

def _show_open_orders() -> None:
    print(f"\n  ── Pending Orders (not yet filled) ───────────────────────")
    try:
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=50)
        orders  = trading_client.get_orders(request)

        if not orders:
            print("  (none)")
            return

        header = f"  {'Symbol':<8} {'Side':<6} {'Type':<20} {'Notional':>10} {'Submitted':>22}"
        print(header)
        print("  " + "─" * 68)

        for o in sorted(orders, key=lambda x: x.symbol):
            side       = o.side.value.upper()
            order_type = f"Limit @ ${float(o.limit_price):.2f}" if o.limit_price else "Market"
            notional   = f"${float(o.notional):.2f}" if o.notional else f"{o.qty} shares"
            submitted  = o.submitted_at.strftime("%b %d, %H:%M:%S") if o.submitted_at else "N/A"

            print(f"  {o.symbol:<8} {side:<6} {order_type:<20} {notional:>10} {submitted:>22}")

    except Exception as e:
        print(f"  ERROR fetching open orders: {e}")


# ── Section 4: Orders filled today ───────────────────────────────────────────

def _show_filled_today() -> None:
    print(f"\n  ── Filled Today ──────────────────────────────────────────")
    try:
        # Fetch last 100 closed orders and filter to those filled today
        request = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=100)
        orders  = trading_client.get_orders(request)

        today = datetime.now(timezone.utc).date()
        filled_today = [
            o for o in orders
            if o.filled_at and o.filled_at.date() == today
            and o.filled_qty and float(o.filled_qty) > 0
        ]

        if not filled_today:
            print("  (no fills today yet)")
            return

        header = f"  {'Symbol':<8} {'Side':<6} {'Fill Price':>11} {'Qty':>10} {'Total':>12} {'Filled At':>20}"
        print(header)
        print("  " + "─" * 68)

        for o in sorted(filled_today, key=lambda x: x.filled_at):
            side       = o.side.value.upper()
            fill_price = float(o.filled_avg_price) if o.filled_avg_price else 0
            qty        = float(o.filled_qty)
            total      = fill_price * qty
            filled_at  = o.filled_at.strftime("%b %d, %H:%M:%S")

            print(f"  {o.symbol:<8} {side:<6} ${fill_price:>10.2f} {qty:>10.4f} "
                  f"${total:>11,.2f} {filled_at:>20}")

    except Exception as e:
        print(f"  ERROR fetching filled orders: {e}")


# ── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Check for --loop N flag
    if "--loop" in sys.argv:
        try:
            interval = int(sys.argv[sys.argv.index("--loop") + 1])
        except (IndexError, ValueError):
            interval = 60

        print(f"Running monitor every {interval}s. Press Ctrl+C to stop.")
        while True:
            show_dashboard()
            time.sleep(interval)
    else:
        show_dashboard()
