"""
config.py - Aria Scalper Configuration
Fast in, fast out. 20-100 trades/day target.
"""
import os

VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]
KRAKEN_PAIRS  = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD"}
TRADINGVIEW_SYMBOLS = {
    "BTCUSD": "BITSTAMP:BTCUSD",
    "ETHUSD": "BITSTAMP:ETHUSD",
}

# Scalper risk - tighter stops = bigger sizes = faster profit
RISK_PER_TRADE_PCT   = 1.0   # 1% per trade (safe for high frequency)
DAILY_LOSS_LIMIT_PCT = 5.0   # Stop if down 5% in a day
MIN_RISK_REWARD      = 1.2   # 1.2:1 minimum - fast exits
SL_ATR_MULTIPLIER    = 0.4   # Very tight stop (0.4x ATR)
TP_ATR_MULTIPLIER    = 0.6   # Quick TP (0.6x ATR)

# Trade frequency
MAX_TRADES_PER_DAY = 100     # Up to 100 trades/day - scalper mode
MAX_OPEN_POSITIONS = 2       # BTC + ETH simultaneously
MIN_CONFIDENCE     = 60      # Lower threshold for more trades

# Position management
BREAKEVEN_USD    = 2.0   # Move SL to BE at +$2
PARTIAL_TP_USD   = 4.0   # Close 50% at +$4
TRAIL_AFTER_USD  = 6.0   # Trail remainder after +$6
TIMEOUT_MINUTES  = 30    # Close if stuck > 30 minutes
MIN_PROFIT_USD   = 0.50  # Min profit to hold past timeout

# Scan speed
SCAN_INTERVAL_SECONDS = 60     # Scan every 60 seconds
DASHBOARD_REFRESH_SECONDS = 30

# Entry conditions
MIN_CANDLE_BODY_RATIO = 0.5   # Body must be 50%+ of range
MIN_TREND_STRENGTH    = 20    # Lower = more trades
MIN_CONFIDENCE        = 55    # Lower = more trades

# Confidence scoring
CONFIDENCE_MAX = {
    "Market Structure": 25,
    "EMA Alignment":    25,
    "RSI":              15,
    "Candle Strength":  20,
    "Volume":           15,
}

BUY_CONDITIONS  = {"market_structure": "Bullish", "ema_alignment": "Bullish",
                   "min_rr": MIN_RISK_REWARD, "min_confidence": MIN_CONFIDENCE}
SELL_CONDITIONS = {"market_structure": "Bearish", "ema_alignment": "Bearish",
                   "min_rr": MIN_RISK_REWARD, "min_confidence": MIN_CONFIDENCE}

MT5_BRIDGE_URL = os.getenv("MT5_BRIDGE_URL", "")
MT5_BRIDGE_KEY = os.getenv("MT5_BRIDGE_KEY", "aria-secret")
USE_MT5        = bool(MT5_BRIDGE_URL)
MIN_TRADES_FOR_RECS = 10
