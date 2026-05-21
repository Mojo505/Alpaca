"""
Report Generator
================
Generates a self-contained HTML dashboard from live Alpaca account data.
Output: report/dashboard.html — pushed to GitHub Pages after every run.

The page auto-refreshes every 5 minutes and shows:
  - Account summary (portfolio value, buying power, day P&L)
  - Open positions with live P&L bars
  - Pending orders
  - Recent filled orders
  - Last check timestamp and status log
"""

import os
from datetime import datetime, timezone

from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus
from utils.alpaca_client import trading_client


def generate(status_lines: list[str] = None) -> str:
    """
    Build the HTML dashboard and write it to report/dashboard.html.
    Returns the file path.
    status_lines: optional list of strings from the latest checkStatus run.
    """
    os.makedirs("report", exist_ok=True)
    filepath = "report/dashboard.html"

    account   = _fetch_account()
    positions = _fetch_positions()
    orders    = _fetch_orders()
    filled    = _fetch_filled_today()

    html = _build_html(account, positions, orders, filled, status_lines or [])

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[Report] Dashboard written → {filepath}")
    return filepath


# ── Data fetchers ────────────────────────────────────────────────────────────

def _fetch_account() -> dict:
    try:
        a = trading_client.get_account()
        clock = trading_client.get_clock()
        equity      = float(a.equity)
        last_equity = float(a.last_equity)
        day_pl      = equity - last_equity
        return {
            "portfolio":   float(a.portfolio_value),
            "buying_power": float(a.buying_power),
            "cash":        float(a.cash),
            "day_pl":      day_pl,
            "day_pl_pct":  (day_pl / last_equity * 100) if last_equity else 0,
            "market_open": clock.is_open,
        }
    except Exception as e:
        return {"error": str(e)}


def _fetch_positions() -> list[dict]:
    try:
        positions = trading_client.get_all_positions()
        return [{
            "symbol":     p.symbol,
            "qty":        float(p.qty),
            "entry":      float(p.avg_entry_price),
            "current":    float(p.current_price),
            "mkt_val":    float(p.market_value),
            "unreal_pl":  float(p.unrealized_pl),
            "unreal_pct": float(p.unrealized_plpc) * 100,
        } for p in positions]
    except Exception:
        return []


def _fetch_orders() -> list[dict]:
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=50)
        orders = trading_client.get_orders(req)
        return [{
            "symbol":    o.symbol,
            "side":      o.side.value.upper(),
            "type":      (f"Limit @ ${float(o.limit_price):.2f}" if o.limit_price
                          else f"Stop @ ${float(o.stop_price):.2f}"  if o.stop_price
                          else "Market"),
            "notional":  float(o.notional) if o.notional else 0,
            "submitted": o.submitted_at.strftime("%b %d %H:%M") if o.submitted_at else "",
        } for o in orders]
    except Exception:
        return []


def _fetch_filled_today() -> list[dict]:
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=50)
        orders = trading_client.get_orders(req)
        today  = datetime.now(timezone.utc).date()
        return [{
            "symbol":     o.symbol,
            "side":       o.side.value.upper(),
            "fill_price": float(o.filled_avg_price) if o.filled_avg_price else 0,
            "qty":        float(o.filled_qty) if o.filled_qty else 0,
            "total":      float(o.filled_avg_price or 0) * float(o.filled_qty or 0),
            "filled_at":  o.filled_at.strftime("%H:%M:%S") if o.filled_at else "",
        } for o in orders
          if o.filled_at and o.filled_at.date() == today
          and o.filled_qty and float(o.filled_qty) > 0]
    except Exception:
        return []


# ── HTML builder ─────────────────────────────────────────────────────────────

def _build_html(
    account: dict,
    positions: list[dict],
    orders: list[dict],
    filled: list[dict],
    status_lines: list[str],
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    mkt = "🟢 OPEN" if account.get("market_open") else "🔴 CLOSED"
    day_pl     = account.get("day_pl", 0)
    day_pl_pct = account.get("day_pl_pct", 0)
    pl_color   = "#22c55e" if day_pl >= 0 else "#ef4444"
    pl_sign    = "▲" if day_pl >= 0 else "▼"

    # Positions HTML
    pos_rows = ""
    for p in positions:
        c     = "#22c55e" if p["unreal_pl"] >= 0 else "#ef4444"
        sign  = "▲" if p["unreal_pl"] >= 0 else "▼"
        pct   = abs(p["unreal_pct"])
        bar_w = min(100, int(pct * 10))
        pos_rows += f"""
        <tr>
          <td><b>{p['symbol']}</b></td>
          <td>${p['entry']:.2f}</td>
          <td>${p['current']:.2f}</td>
          <td>${p['mkt_val']:,.2f}</td>
          <td style="color:{c}">
            {sign} ${abs(p['unreal_pl']):.2f}
            <div style="height:4px;width:{bar_w}%;background:{c};border-radius:2px;margin-top:3px"></div>
          </td>
          <td style="color:{c}">{p['unreal_pct']:+.2f}%</td>
        </tr>"""

    pos_section = f"""
    <table>
      <thead><tr>
        <th>Symbol</th><th>Entry</th><th>Current</th>
        <th>Mkt Value</th><th>Unreal P&L</th><th>%</th>
      </tr></thead>
      <tbody>{pos_rows or '<tr><td colspan="6" class="empty">No open positions</td></tr>'}</tbody>
    </table>""" if True else ""

    # Orders HTML
    order_rows = ""
    for o in orders:
        side_color = "#22c55e" if o["side"] == "BUY" else "#f97316"
        order_rows += f"""
        <tr>
          <td><b>{o['symbol']}</b></td>
          <td style="color:{side_color}">{o['side']}</td>
          <td>{o['type']}</td>
          <td>${o['notional']:,.2f}</td>
          <td>{o['submitted']}</td>
        </tr>"""

    # Filled HTML
    filled_rows = ""
    for f in filled:
        side_color = "#22c55e" if f["side"] == "BUY" else "#f97316"
        filled_rows += f"""
        <tr>
          <td><b>{f['symbol']}</b></td>
          <td style="color:{side_color}">{f['side']}</td>
          <td>${f['fill_price']:.2f}</td>
          <td>{f['qty']:.4f}</td>
          <td>${f['total']:,.2f}</td>
          <td>{f['filled_at']}</td>
        </tr>"""

    # Status log HTML
    log_html = "\n".join(
        f'<div class="log-line">{line}</div>'
        for line in (status_lines[-50:] if status_lines else ["No status log yet"])
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="300">
  <title>Trading Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f172a; color: #e2e8f0; padding: 20px; }}
    h1   {{ font-size: 1.4rem; color: #94a3b8; margin-bottom: 4px; }}
    h2   {{ font-size: 1rem; color: #64748b; margin: 24px 0 12px;
             text-transform: uppercase; letter-spacing: .08em; }}
    .meta {{ font-size: .8rem; color: #475569; margin-bottom: 24px; }}
    .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; }}
    .card  {{ background: #1e293b; border-radius: 12px; padding: 20px;
               flex: 1; min-width: 180px; }}
    .card .label {{ font-size: .75rem; color: #64748b; margin-bottom: 6px; }}
    .card .value {{ font-size: 1.5rem; font-weight: 700; }}
    table  {{ width: 100%; border-collapse: collapse; background: #1e293b;
               border-radius: 12px; overflow: hidden; margin-bottom: 8px; }}
    th     {{ background: #0f172a; padding: 10px 14px; text-align: left;
               font-size: .75rem; color: #64748b; text-transform: uppercase; }}
    td     {{ padding: 10px 14px; border-top: 1px solid #1e293b;
               font-size: .875rem; background: #1e293b; }}
    tr:hover td {{ background: #263347; }}
    .empty {{ color: #475569; font-style: italic; text-align: center; padding: 20px; }}
    .log   {{ background: #1e293b; border-radius: 12px; padding: 16px;
               font-family: monospace; font-size: .8rem; color: #94a3b8;
               max-height: 300px; overflow-y: auto; }}
    .log-line {{ padding: 2px 0; border-bottom: 1px solid #0f172a; }}
    .badge-open   {{ color: #22c55e; font-weight: 700; }}
    .badge-closed {{ color: #ef4444; font-weight: 700; }}
    @media(max-width:600px) {{ .card {{ min-width: 140px; }} }}
  </style>
</head>
<body>

  <h1>📊 Trading Dashboard</h1>
  <div class="meta">
    Last updated: {now} &nbsp;|&nbsp;
    Market: <span class="{'badge-open' if account.get('market_open') else 'badge-closed'}">{mkt}</span>
    &nbsp;|&nbsp; Auto-refresh every 5 min
  </div>

  <div class="cards">
    <div class="card">
      <div class="label">Portfolio Value</div>
      <div class="value">${account.get('portfolio', 0):,.2f}</div>
    </div>
    <div class="card">
      <div class="label">Buying Power</div>
      <div class="value">${account.get('buying_power', 0):,.2f}</div>
    </div>
    <div class="card">
      <div class="label">Day P&L</div>
      <div class="value" style="color:{pl_color}">
        {pl_sign} ${abs(day_pl):,.2f}
        <span style="font-size:.9rem">({day_pl_pct:+.2f}%)</span>
      </div>
    </div>
    <div class="card">
      <div class="label">Open Positions</div>
      <div class="value">{len(positions)}</div>
    </div>
    <div class="card">
      <div class="label">Pending Orders</div>
      <div class="value">{len(orders)}</div>
    </div>
  </div>

  <h2>Open Positions</h2>
  {pos_section}

  <h2>Pending Orders</h2>
  <table>
    <thead><tr><th>Symbol</th><th>Side</th><th>Type</th><th>Notional</th><th>Submitted</th></tr></thead>
    <tbody>{order_rows or '<tr><td colspan="5" class="empty">No pending orders</td></tr>'}</tbody>
  </table>

  <h2>Filled Today</h2>
  <table>
    <thead><tr><th>Symbol</th><th>Side</th><th>Fill Price</th><th>Qty</th><th>Total</th><th>Time</th></tr></thead>
    <tbody>{filled_rows or '<tr><td colspan="6" class="empty">No fills today yet</td></tr>'}</tbody>
  </table>

  <h2>Latest Status Log</h2>
  <div class="log">{log_html}</div>

</body>
</html>"""
