"""
Agent 1 — Market Scanner
========================
WHAT IT DOES:
  Connects to Alpaca, fetches all US equities, then applies two filters:
    1. Price  → drop anything under SCAN_MIN_PRICE  (avoids penny stocks)
    2. Volume → drop anything under SCAN_MIN_VOLUME (ensures liquidity)

OUTPUT:
  A list of ticker symbols (strings) ready for Agent 2 (Analyst).

HOW TO RUN STANDALONE:
  python agents/scanner.py
"""

import time
import config
from utils.alpaca_client import trading_client, data_client

from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetClass
from alpaca.data.requests import StockSnapshotRequest
from alpaca.data.enums import DataFeed


def scan_market() -> list[str]:
    """
    Main entry point. Returns filtered list of tradeable symbols.
    Called by the orchestrator — takes ~10-20 seconds due to API calls.
    """
    print("[Scanner] Fetching all US equity assets from Alpaca...")
    raw_assets = _get_tradeable_assets()
    print(f"[Scanner] {len(raw_assets)} tradeable assets found")

    print("[Scanner] Fetching real-time snapshots (price + volume)...")
    filtered = _filter_by_snapshot(raw_assets)
    print(f"[Scanner] {len(filtered)} symbols passed price + volume filters")

    return filtered[:config.SCAN_MAX_RESULTS]


def _get_tradeable_assets() -> list[str]:
    """
    Step 1: Ask Alpaca for all US equity assets.
    Filter to only assets that are:
      - active       (not delisted)
      - tradable     (can actually place orders)
      - fractionable (supports dollar-based orders, not just whole shares)
    """
    request = GetAssetsRequest(asset_class=AssetClass.US_EQUITY)
    all_assets = trading_client.get_all_assets(request)

    return [
        asset.symbol
        for asset in all_assets
        if asset.status.value == "active"
        and asset.tradable
        and asset.fractionable
    ]


def _filter_by_snapshot(symbols: list[str]) -> list[str]:
    """
    Step 2: Fetch real-time snapshots in batches.
    A snapshot gives us today's price and volume without downloading full bar history.
    Alpaca limits snapshot requests to 1000 symbols at a time.

    We check:
      - latest_trade.price  > SCAN_MIN_PRICE   (e.g. above $10)
      - daily_bar.volume    > SCAN_MIN_VOLUME   (e.g. above 500,000 shares/day)
    """
    passed = []
    batch_size = 500   # stay safely under Alpaca's 1000-symbol limit

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]

        try:
            request = StockSnapshotRequest(symbol_or_symbols=batch, feed=DataFeed.IEX)
            snapshots = data_client.get_stock_snapshot(request)

            for symbol, snap in snapshots.items():
                price  = snap.latest_trade.price  if snap.latest_trade  else 0
                volume = snap.daily_bar.volume     if snap.daily_bar     else 0

                if price >= config.SCAN_MIN_PRICE and volume >= config.SCAN_MIN_VOLUME:
                    passed.append(symbol)

        except Exception as e:
            print(f"[Scanner] Warning: snapshot batch failed — {e}")

        # Be polite to the API: short pause between batches
        time.sleep(0.3)

    return passed


def _print_results(symbols: list[str]) -> None:
    """Pretty-print the scan results (used when running standalone)."""
    print("\n" + "─" * 40)
    print(f"SCANNER RESULTS — {len(symbols)} symbols passed")
    print("─" * 40)
    for i, sym in enumerate(symbols, 1):
        print(f"  {i:>3}. {sym}")
    print("─" * 40)
    print("These symbols will be passed to Agent 2 (Analyst).\n")


# ── Standalone runner ────────────────────────────────────────────────────────
# Run `python agents/scanner.py` to test this agent alone
if __name__ == "__main__":
    results = scan_market()
    _print_results(results)
