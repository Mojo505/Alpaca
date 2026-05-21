"""
Telegram Notifier
=================
Sends messages to your Telegram chat when important trading events happen.

Setup (one-time):
  1. Open Telegram → search @BotFather → send /newbot
  2. Follow prompts → BotFather gives you a TOKEN
  3. Send any message to your new bot
  4. Visit: https://api.telegram.org/bot<TOKEN>/getUpdates
  5. Copy the "id" value from "chat" → that is your CHAT_ID
  6. Add both to GitHub Secrets:  TELEGRAM_BOT_TOKEN  and  TELEGRAM_CHAT_ID

All functions are safe to call even if credentials are missing —
they'll log a warning and return False instead of crashing.
"""

import os
import requests


TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send(message: str, silent: bool = False) -> bool:
    """
    Send a plain or HTML-formatted message to your Telegram chat.
    silent=True → notification arrives without sound (good for routine updates).
    Returns True if sent successfully.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] Skipped — TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")
        return False

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":              TELEGRAM_CHAT_ID,
                "text":                 message,
                "parse_mode":           "HTML",
                "disable_notification": silent,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        else:
            print(f"[Telegram] Failed ({resp.status_code}): {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"[Telegram] ERROR: {e}")
        return False


# ── Pre-built message builders ───────────────────────────────────────────────

def notify_orders_placed(picks: list[dict], total: float) -> None:
    """Called after orchestrator places new orders."""
    lines = [f"🟢 <b>Orders Placed — ${total:,.2f} deployed</b>\n"]
    for p in picks:
        amt = total * p["position_weight"]
        entry = p.get("entry_price")
        entry_str = f"limit @ ${entry:.2f}" if entry else "market"
        lines.append(f"  • <b>{p['symbol']}</b>  ${amt:,.2f}  ({entry_str})")
    send("\n".join(lines))


def notify_position_update(symbol: str, current: float, entry: float,
                            pl: float, pl_pct: float, note: str = "") -> None:
    """Called when a position has a notable change."""
    icon = "📈" if pl >= 0 else "📉"
    msg  = (
        f"{icon} <b>{symbol}</b>\n"
        f"  Entry: ${entry:.2f} → Now: ${current:.2f}\n"
        f"  P&L: {'▲' if pl>=0 else '▼'} ${abs(pl):.2f} ({pl_pct:+.2f}%)"
    )
    if note:
        msg += f"\n  ⚠️ {note}"
    send(msg)


def notify_adjustment(symbol: str, action: str, detail: str) -> None:
    """Called when checkStatus makes an order adjustment."""
    icons = {
        "take_profit":   "🎯",
        "stop_loss":     "🛡️",
        "trailing_stop": "🔁",
        "adjusted":      "✏️",
        "cancelled":     "❌",
    }
    icon = icons.get(action, "🔔")
    send(f"{icon} <b>{symbol}</b> — {detail}")


def notify_check_summary(
    positions: int,
    pending: int,
    adjustments_made: int,
    warnings: int,
) -> None:
    """Routine 30-min check-in summary (sent silently — no sound)."""
    msg = (
        f"🔄 <b>Status Check</b>\n"
        f"  Positions: {positions}  |  Pending: {pending}\n"
        f"  Adjustments: {adjustments_made}  |  Warnings: {warnings}"
    )
    send(msg, silent=True)   # silent = no phone buzz for routine updates


def notify_warning(symbol: str, message: str) -> None:
    """High-priority alert — always with sound."""
    send(f"⚠️ <b>WARNING — {symbol}</b>\n{message}", silent=False)


def notify_pipeline_error(step: str, error: str) -> None:
    """Called if any agent crashes during the pipeline."""
    send(f"🔴 <b>Pipeline Error at {step}</b>\n<code>{error[:300]}</code>")
