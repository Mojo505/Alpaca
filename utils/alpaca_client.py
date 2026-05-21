"""
Shared Alpaca clients — import these anywhere instead of re-creating connections.
"""
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
import config

# TradingClient  → place orders, check account, get positions
# paper=True     → uses the free paper-trading sandbox (safe to experiment)
trading_client = TradingClient(
    config.ALPACA_API_KEY,
    config.ALPACA_SECRET_KEY,
    paper=True,
)

# StockHistoricalDataClient → price bars, quotes, snapshots (read-only market data)
data_client = StockHistoricalDataClient(
    config.ALPACA_API_KEY,
    config.ALPACA_SECRET_KEY,
)
