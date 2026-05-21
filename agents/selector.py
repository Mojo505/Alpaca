"""
Agent 3 — Selector
==================
WHAT IT DOES:
  This is the "brain" agent. It takes the scored symbols from Agent 2
  and uses Claude LLM to make the final investment decision.

  Why Claude and not just "pick top 4"?
    - Score ties need judgment (many stocks scored 8/8)
    - Avoid doubling up on correlated assets (e.g. TSLA + TSLL)
    - Avoid sector concentration (don't buy 4 tech stocks)
    - Apply risk rules a simple score can't express
    - Produce human-readable reasoning for every pick

OUTPUT:
  List of exactly 4 dicts:
  {
    "symbol":          "TSLA",
    "reasoning":       "Strong momentum across all 4 indicators...",
    "position_weight": 0.25,    ← must sum to 1.0 across all 4 picks
    "risk_note":       "High beta — volatile on news days"
  }

HOW TO RUN STANDALONE:
  python agents/selector.py
"""

import json
import re
import anthropic
import config


client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


# ── Public entry point ───────────────────────────────────────────────────────

def select_stocks(scored_symbols: list[dict]) -> list[dict]:
    """
    Feed top candidates to Claude. Claude reviews signals and
    returns the best 4 picks with reasoning and position weights.
    Called by the orchestrator after Agent 2 completes.
    """
    # Only send Claude the top 15 — beyond that the scores are too low to matter
    candidates = scored_symbols[:15]

    print(f"[Selector] Sending top {len(candidates)} candidates to Claude...")
    prompt = _build_prompt(candidates)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )

    raw_text = response.content[0].text
    picks = _parse_response(raw_text)

    print(f"[Selector] Claude selected {len(picks)} stocks.")
    return picks


# ── Prompt builder ───────────────────────────────────────────────────────────

def _build_prompt(candidates: list[dict]) -> str:
    """
    Build a detailed prompt for Claude.

    Good prompting principles used here:
      1. Give Claude a clear ROLE (quantitative portfolio manager)
      2. Explain the scoring system so Claude understands the data
      3. Give explicit RULES to follow (no correlated assets, etc.)
      4. Specify the exact OUTPUT FORMAT — JSON makes parsing reliable
      5. Ask for reasoning — it improves Claude's decision quality
    """

    # Format the candidate data cleanly for the prompt
    candidates_text = ""
    for i, item in enumerate(candidates, 1):
        s = item["signals"]
        candidates_text += f"""
{i}. {item['symbol']}  (Score: {item['score']}/10)
   RSI:         {s.get('rsi', 'N/A')} — {s.get('rsi_signal', '')}
   EMA:         {s.get('ema_signal', 'N/A')}
   MA-20:       {"Above" if s.get('above_ma20') else "Below"}  |  MA-50: {"Above" if s.get('above_ma50') else "Below"}
   Price:       ${s.get('price', 'N/A')}
   Fibonacci:   {s.get('fib_signal', 'N/A')}
   Entry price: ${s.get('fib_entry_price', 'N/A')}  ({s.get('fib_entry_type', 'market')} order)
   Alligator:   {s.get('alligator_state', 'N/A')}
               Jaw=${s.get('alligator_jaw','N/A')}  Teeth=${s.get('alligator_teeth','N/A')}  Lips=${s.get('alligator_lips','N/A')}
               Spread widening: {s.get('alligator_widening', 'N/A')}
"""

    return f"""You are a quantitative portfolio manager running a momentum-based US equity strategy.

Below are the top {len(candidates)} stocks ranked by technical analysis score (max 10/10).
Each was scored on: RSI momentum, EMA crossover, Moving Average position, Fibonacci retracement, and Williams Alligator.

UNDERSTANDING THE ALLIGATOR INDICATOR:
  The Williams Alligator has three lines — Jaw (slow), Teeth (medium), Lips (fast).
  Use it to assess market state BEFORE deciding:

  "Eating bullish — strong uptrend, accelerating"  → BEST entry. Trend is strong and growing.
  "Awakening bullish — uptrend forming"            → GOOD entry. Trend just starting.
  "Sated bullish — uptrend weakening"              → CAUTION. Trend may be ending, reduce weight.
  "Sleeping — market consolidating, no trend"      → AVOID. No trend = no edge. Skip this stock.
  "Eating bearish"  or  "Awakening bearish"        → AVOID. Market moving against you.
  "Transitioning"                                  → NEUTRAL. Use other indicators to decide.

  Widening spread = trend accelerating (more confidence)
  Converging spread = trend slowing (less confidence)

--- CANDIDATE STOCKS ---
{candidates_text}
--- END CANDIDATES ---

Your task: Select exactly 4 stocks to invest in today.

RULES you must follow:
1. NO correlated pairs — do not pick both a stock and its leveraged ETF (e.g. TSLA + TSLL). Pick one or the other.
2. NO sector concentration — avoid picking more than 2 stocks from the same sector.
3. ETFs (EEM, EFA, IWM, QQQ, SPY, etc.) count as ONE category — max 1 ETF in the final 4.
4. Prefer stocks with RSI in the 45–65 range (momentum without being overbought).
5. AVOID stocks where Alligator is Sleeping, Bearish, or Sated — even if other indicators look good.
6. Give HIGHER position weights to stocks where Alligator is "Eating bullish + widening spread".
7. All 4 position_weights must sum to exactly 1.0.

Respond with ONLY a valid JSON array — no explanation before or after the JSON block.
Format:
[
  {{
    "symbol": "TICKER",
    "reasoning": "2-3 sentence explanation covering score, Alligator state, and why this was chosen",
    "position_weight": 0.30,
    "risk_note": "One sentence on the key risk to watch",
    "entry_price": 284.50,
    "entry_type": "limit"
  }},
  ...
]

For entry_price and entry_type: use the values provided in the candidate data above.
If entry_type is "limit", the order will only fill if price reaches that level.
If entry_type is "market", the order fills immediately at current price.
"""


# ── Response parser ──────────────────────────────────────────────────────────

def _parse_response(raw_text: str) -> list[dict]:
    """
    Extract and validate the JSON from Claude's response.

    Claude occasionally wraps JSON in markdown code blocks (```json ... ```)
    so we strip those before parsing.
    We also validate that weights sum to ~1.0.
    """
    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_text).strip().rstrip("```").strip()

    try:
        picks = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[Selector] ERROR: Could not parse Claude's response as JSON — {e}")
        print(f"[Selector] Raw response was:\n{raw_text}")
        return []

    # Validate structure
    if not isinstance(picks, list) or len(picks) == 0:
        print("[Selector] ERROR: Claude returned an empty or invalid list.")
        return []

    # Validate weights sum to ~1.0
    total_weight = sum(p.get("position_weight", 0) for p in picks)
    if not (0.98 <= total_weight <= 1.02):
        print(f"[Selector] WARNING: Weights sum to {total_weight:.2f}, expected 1.0. "
              "Normalizing...")
        for p in picks:
            p["position_weight"] = round(p["position_weight"] / total_weight, 4)

    return picks


# ── Pretty printer ───────────────────────────────────────────────────────────

def _print_results(picks: list[dict]) -> None:
    print("\n" + "═" * 60)
    print("  SELECTOR RESULTS — Claude's Final 4 Picks")
    print("═" * 60)
    for i, pick in enumerate(picks, 1):
        weight_pct = pick['position_weight'] * 100
        bar = "█" * int(weight_pct / 5)   # visual weight bar (each █ = 5%)
        print(f"""
  {i}. {pick['symbol']:<6}  Weight: {weight_pct:.1f}%  {bar}
     Reasoning:  {pick['reasoning']}
     Risk note:  {pick.get('risk_note', 'N/A')}
""")
    total = sum(p['position_weight'] for p in picks)
    print(f"  Total allocation: {total * 100:.1f}%")
    print("═" * 60)
    print("  These picks will be passed to Agent 4 (Order Placer).\n")


# ── Standalone runner ────────────────────────────────────────────────────────

if __name__ == "__main__":
    from agents.scanner import scan_market
    from agents.analyst import analyze

    print("Testing Agent 3 — Selector\n")

    print("Step 1: Scanning market...")
    symbols = scan_market()

    print(f"\nStep 2: Analyzing {len(symbols)} symbols...")
    scored = analyze(symbols)

    print(f"\nStep 3: Asking Claude to select the best 4...\n")
    picks = select_stocks(scored)

    if picks:
        _print_results(picks)
    else:
        print("ERROR: Selector returned no picks.")
