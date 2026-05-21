import os
from dotenv import load_dotenv

load_dotenv()

ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Scanner filters — tweak these to widen or narrow the candidate pool
SCAN_MIN_PRICE   = 10.0      # ignore anything below $10 (penny stocks)
SCAN_MIN_VOLUME  = 500_000   # minimum average daily volume
SCAN_MAX_RESULTS = 50        # cap on how many symbols pass to the analyst

# Order placer — controls how much of your buying power is used per run
# 0.20 = use 20% of buying power, split across the 4 picks
# Start small (0.05–0.10) until you trust the system
POSITION_BUDGET = 0.20
