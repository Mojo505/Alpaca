"""
Agent 2 — Analyst
=================
WHAT IT DOES:
  Takes the symbol list from Agent 1 (Scanner) and scores each stock
  using five technical indicators:

    1. RSI        (Relative Strength Index)   → momentum
    2. EMA        (Exponential Moving Avg)    → short-term trend
    3. MA         (Simple Moving Average)     → long-term trend
    4. Fibonacci  Retracement                → support/resistance levels
    5. Alligator  (Williams Alligator)        → market state / trend strength

  Each indicator contributes points to a score (max 10 points).
  Symbols are returned sorted best → worst.

OUTPUT:
  List of dicts:  { symbol, score, signals }
  signals contains the raw indicator values so Agent 3 can explain them.

HOW TO RUN STANDALONE:
  python agents/analyst.py
"""

import time
import pandas as pd
from datetime import datetime, timedelta

from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from utils.alpaca_client import data_client
import config


# ── Public entry point ───────────────────────────────────────────────────────

def analyze(symbols: list[str]) -> list[dict]:
    """
    Score every symbol using 4 indicators.
    Returns list sorted by score (highest first).
    Called by the orchestrator — takes ~30-60s depending on symbol count.
    """
    print(f"[Analyst] Fetching 90-day price history for {len(symbols)} symbols...")
    bars_by_symbol = _fetch_bars(symbols)
    print(f"[Analyst] Got data for {len(bars_by_symbol)} symbols, scoring now...")

    scored = []
    for symbol, df in bars_by_symbol.items():
        if len(df) < 26:          # need at least 26 bars for MACD/EMA to be meaningful
            continue
        score, signals = _score(df)
        scored.append({
            "symbol":  symbol,
            "score":   score,
            "signals": signals,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    print(f"[Analyst] Scoring complete. Top symbol: {scored[0]['symbol']} "
          f"(score {scored[0]['score']}/10)" if scored else "[Analyst] No results.")
    return scored


# ── Step 1: Fetch price bars ─────────────────────────────────────────────────

def _fetch_bars(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """
    Download 90 days of daily OHLCV bars for all symbols in one API call.
    Returns a dict:  { "AAPL": DataFrame, "TSLA": DataFrame, ... }

    Why 90 days?
      - RSI needs 14 bars to warm up
      - MA-50 needs 50 bars
      - Fibonacci needs a meaningful high/low window
      90 days gives all indicators enough history.
    """
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=datetime.now() - timedelta(days=90),
        end=datetime.now(),
        feed=DataFeed.IEX,   # IEX feed is free; SIP requires a paid subscription
    )

    try:
        bars_df = data_client.get_stock_bars(request).df
    except Exception as e:
        print(f"[Analyst] ERROR fetching bars: {e}")
        return {}

    result = {}
    for symbol in symbols:
        try:
            df = bars_df.loc[symbol].copy().reset_index()
            df = df.sort_values("timestamp").reset_index(drop=True)
            result[symbol] = df
        except KeyError:
            pass   # symbol had no data in this window

    return result


# ── Step 2: Score each symbol ────────────────────────────────────────────────

def _score(df: pd.DataFrame) -> tuple[float, dict]:
    """
    Run all 5 indicators and return (total_score, signals_dict).
    Max score = 10 points.

    Scoring logic:
      RSI        +1 if RSI > 40   (has momentum, not dead)
                 +1 if RSI < 70   (not overbought yet)

      EMA cross  +2 if EMA-9 > EMA-21   (short-term bullish crossover)

      MA         +1 if price > MA-20    (above short-term trend line)
                 +1 if price > MA-50    (above long-term trend line)

      Fibonacci  +1 if price is near a Fib support level (within 2%)
                 +1 if price bounced UP from that support (confirmation)

      Alligator  +1 if Lips > Teeth > Jaw (bullish trend order)
                 +1 if the spread between Lips and Jaw is widening (trend strengthening)
    """
    close = df["close"]
    score = 0
    signals = {}

    # ── Indicator 1: RSI ─────────────────────────────────────────────────────
    rsi_val = _rsi(close, period=14)
    signals["rsi"] = round(rsi_val, 2)

    if rsi_val > 40:  score += 1   # momentum exists
    if rsi_val < 70:  score += 1   # not yet overbought

    signals["rsi_signal"] = (
        "Overbought"    if rsi_val >= 70 else
        "Oversold"      if rsi_val <= 30 else
        "Healthy range"
    )

    # ── Indicator 2: EMA crossover ───────────────────────────────────────────
    ema9  = close.ewm(span=9,  adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema_bullish = ema9.iloc[-1] > ema21.iloc[-1]

    signals["ema_9"]       = round(ema9.iloc[-1], 2)
    signals["ema_21"]      = round(ema21.iloc[-1], 2)
    signals["ema_signal"]  = "Bullish crossover" if ema_bullish else "Bearish crossover"

    if ema_bullish:  score += 2

    # ── Indicator 3: Simple Moving Averages ──────────────────────────────────
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    price_now = close.iloc[-1]

    signals["ma_20"]        = round(ma20.iloc[-1], 2)
    signals["ma_50"]        = round(ma50.iloc[-1], 2)
    signals["price"]        = round(price_now, 2)
    signals["above_ma20"]   = price_now > ma20.iloc[-1]
    signals["above_ma50"]   = price_now > ma50.iloc[-1]

    if price_now > ma20.iloc[-1]:  score += 1
    if price_now > ma50.iloc[-1]:  score += 1

    # ── Indicator 4: Fibonacci Retracement ───────────────────────────────────
    fib_score, fib_signals = _fibonacci(df)
    score += fib_score
    signals.update(fib_signals)

    # ── Indicator 5: Williams Alligator ──────────────────────────────────────
    ali_score, ali_signals = _alligator(df)
    score += ali_score
    signals.update(ali_signals)

    return score, signals


# ── Indicator helpers ────────────────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> float:
    """
    Standard RSI formula:
      1. Calculate daily price changes
      2. Separate gains and losses
      3. Average over 'period' days
      4. RSI = 100 - (100 / (1 + avg_gain/avg_loss))

    Returns the most recent RSI value.
    """
    delta  = close.diff()
    gain   = delta.clip(lower=0).rolling(period).mean()
    loss   = (-delta.clip(upper=0)).rolling(period).mean()
    rs     = gain / loss
    rsi    = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]


def _fibonacci(df: pd.DataFrame) -> tuple[float, dict]:
    """
    Fibonacci Retracement — How it works:
      1. Find the swing HIGH and swing LOW over the last 60 bars
      2. Calculate the standard retracement levels:
           23.6%,  38.2%,  50.0%,  61.8%,  78.6%
      3. Check if today's price is sitting NEAR a support level (within 2%)
      4. Check if price moved UP from that level (bounce confirmation)

    Why Fibonacci matters:
      Traders worldwide watch these levels. When price pulls back to
      the 38.2% or 61.8% level and bounces, that's considered a
      strong buy signal because many traders act on it simultaneously.

    Scoring:
      +1 if price is near ANY Fibonacci support level (within 2%)
      +1 if there's also an upward bounce (close > open on that bar)
    """
    high  = df["high"].rolling(60).max().iloc[-1]
    low   = df["low"].rolling(60).min().iloc[-1]
    price = df["close"].iloc[-1]
    swing = high - low

    if swing == 0:
        return 0, {"fib_signal": "No swing detected"}

    # Standard Fibonacci levels (price pulls back from high to these levels)
    levels = {
        "23.6%": high - 0.236 * swing,
        "38.2%": high - 0.382 * swing,
        "50.0%": high - 0.500 * swing,
        "61.8%": high - 0.618 * swing,
        "78.6%": high - 0.786 * swing,
    }

    # Is price within 2% of any level?
    near_level = None
    for label, level_price in levels.items():
        distance_pct = abs(price - level_price) / level_price
        if distance_pct <= 0.02:
            near_level = label
            break

    # Did the last bar close UP (bounce confirmation)?
    last_bar = df.iloc[-1]
    bounced_up = last_bar["close"] > last_bar["open"]

    fib_score = 0
    if near_level:          fib_score += 1
    if near_level and bounced_up: fib_score += 1

    # Entry price logic:
    #   If price is near a Fib level → use that level as the limit entry price
    #   If price is not near any level → use current price (market entry)
    entry_price = round(levels[near_level], 2) if near_level else round(price, 2)

    signals = {
        "fib_swing_high":  round(high, 2),
        "fib_swing_low":   round(low, 2),
        "fib_levels":      {k: round(v, 2) for k, v in levels.items()},
        "fib_near_level":  near_level or "None",
        "fib_bounce":      bounced_up if near_level else False,
        "fib_entry_price": entry_price,   # ← Agent 4 will use this as limit price
        "fib_entry_type":  "limit" if near_level else "market",
        "fib_signal": (
            f"Near {near_level} support + bounce confirmed" if near_level and bounced_up else
            f"Near {near_level} support — no bounce yet"   if near_level else
            "No Fibonacci support nearby"
        ),
    }

    return fib_score, signals


def _smma(series: pd.Series, period: int) -> pd.Series:
    """
    Smoothed Moving Average (SMMA) — the moving average type used inside
    the Williams Alligator. Different from EMA:

      - First value:  simple average of the first 'period' bars
      - Each after:   SMMA[i] = (SMMA[i-1] × (period-1) + close[i]) / period

    This gives a smoother, slower-reacting line than EMA, which is what
    Bill Williams wanted — Alligator lines should be "lazy" and only react
    to strong, sustained moves.
    """
    result = series.copy().astype(float)
    # Seed with SMA for the first window
    result.iloc[:period] = series.iloc[:period].mean()
    for i in range(period, len(series)):
        result.iloc[i] = (result.iloc[i - 1] * (period - 1) + series.iloc[i]) / period
    return result


def _alligator(df: pd.DataFrame) -> tuple[float, dict]:
    """
    Williams Alligator — How it works:
    ====================================
    Bill Williams designed this to identify whether the market is trending
    (and in which direction) or sleeping (consolidating, no trend).

    THREE lines, each a Smoothed Moving Average with a time-shift:

      JAW   (Blue)  = SMMA(13), shifted 8 bars forward
                      → the slowest line, represents the big trend
      TEETH (Red)   = SMMA(8),  shifted 5 bars forward
                      → medium speed
      LIPS  (Green) = SMMA(5),  shifted 3 bars forward
                      → the fastest line, reacts first to price changes

    The shift means: at today's bar, what you see is:
      Jaw   = SMMA(13) value from 8 bars ago
      Teeth = SMMA(8)  value from 5 bars ago
      Lips  = SMMA(5)  value from 3 bars ago

    ALLIGATOR STATES:
      SLEEPING   → lines are tangled / very close together
                   Market is ranging. No trade.

      AWAKENING  → lines starting to separate
                   Trend may be forming. Watch closely.

      EATING     → lines fully separated in order:
                   Bullish: LIPS > TEETH > JAW  (uptrend)
                   Bearish: LIPS < TEETH < JAW  (downtrend)

      SATED      → lines converging after being separated
                   Trend is exhausted. Consider exiting.

    SCORING:
      +1 if Alligator is EATING BULLISH   (lips > teeth > jaw)
      +1 if the spread is WIDENING        (trend is accelerating)
       0 if SLEEPING, AWAKENING, or SATED (no strong trend signal)
    """
    close = df["close"]

    # Need at least 21 bars (13 SMMA + 8 shift) for jaw to be meaningful
    if len(close) < 25:
        return 0, {"alligator_state": "Insufficient data"}

    # Calculate the three SMMA lines
    smma13 = _smma(close, 13)
    smma8  = _smma(close, 8)
    smma5  = _smma(close, 5)

    # Apply the time shifts:
    # At the current bar, the displayed values are from N bars back
    jaw   = smma13.iloc[-9]   # 8 bars back  (shift=8,  index = -1-8 = -9)
    teeth = smma8.iloc[-6]    # 5 bars back  (shift=5,  index = -1-5 = -6)
    lips  = smma5.iloc[-4]    # 3 bars back  (shift=3,  index = -1-3 = -4)

    # Previous values (5 bars earlier) — used to detect widening/converging
    jaw_prev   = smma13.iloc[-14]  # 8+5 bars back
    teeth_prev = smma8.iloc[-11]   # 5+5 bars back
    lips_prev  = smma5.iloc[-9]    # 3+5 bars back

    price_now = close.iloc[-1]

    # ── Determine state ───────────────────────────────────────────────────────
    spread_now  = abs(lips - jaw)
    spread_prev = abs(lips_prev - jaw_prev)
    sleep_threshold = price_now * 0.005   # lines within 0.5% of each other = sleeping

    is_bullish_order = lips > teeth > jaw
    is_bearish_order = lips < teeth < jaw
    is_sleeping      = spread_now < sleep_threshold
    is_widening      = spread_now > spread_prev   # trend gaining strength
    is_converging    = spread_now < spread_prev   # trend losing strength

    if is_sleeping:
        state = "Sleeping — market consolidating, no trend"
    elif is_bullish_order and is_widening:
        state = "Eating bullish — strong uptrend, accelerating"
    elif is_bullish_order and is_converging:
        state = "Sated bullish — uptrend weakening"
    elif is_bullish_order:
        state = "Awakening bullish — uptrend forming"
    elif is_bearish_order and is_widening:
        state = "Eating bearish — strong downtrend, accelerating"
    elif is_bearish_order and is_converging:
        state = "Sated bearish — downtrend weakening"
    elif is_bearish_order:
        state = "Awakening bearish — downtrend forming"
    else:
        state = "Transitioning — lines mixed, no clear trend"

    # ── Score ─────────────────────────────────────────────────────────────────
    ali_score = 0
    if is_bullish_order:  ali_score += 1   # correct trend direction
    if is_bullish_order and is_widening: ali_score += 1   # trend accelerating

    signals = {
        "alligator_jaw":       round(jaw,   2),
        "alligator_teeth":     round(teeth, 2),
        "alligator_lips":      round(lips,  2),
        "alligator_spread":    round(spread_now, 4),
        "alligator_widening":  is_widening,
        "alligator_state":     state,
        "alligator_bullish":   is_bullish_order,
        "alligator_bearish":   is_bearish_order,
    }

    return ali_score, signals


# ── Pretty printer ───────────────────────────────────────────────────────────

def _print_results(scored: list[dict]) -> None:
    print("\n" + "─" * 60)
    print(f"ANALYST RESULTS — Top {min(10, len(scored))} of {len(scored)} symbols")
    print("─" * 60)
    for item in scored[:10]:
        sym  = item["symbol"]
        sc   = item["score"]
        s    = item["signals"]
        bar  = "█" * sc + "░" * (10 - sc)
        print(f"\n  {sym:<6}  Score: {sc}/10  [{bar}]")
        print(f"    RSI:       {s['rsi']:>6.1f}  ({s['rsi_signal']})")
        print(f"    EMA:       {s['ema_signal']}")
        print(f"    MA20:      {'above' if s['above_ma20'] else 'below'} | "
              f"MA50: {'above' if s['above_ma50'] else 'below'}")
        print(f"    Fib:       {s['fib_signal']}")
        print(f"    Alligator: {s['alligator_state']}")
    print("\n" + "─" * 60)
    print("These symbols (sorted) will be passed to Agent 3 (Selector).\n")


# ── Standalone runner ────────────────────────────────────────────────────────

if __name__ == "__main__":
    from agents.scanner import scan_market

    print("Testing Agent 2 — Analyst\n")
    print("Step 1: Running Scanner to get symbols...")
    symbols = scan_market()

    print(f"\nStep 2: Analyzing {len(symbols)} symbols with 4 indicators...\n")
    results = analyze(symbols)

    _print_results(results)
