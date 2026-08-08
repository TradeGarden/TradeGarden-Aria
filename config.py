"""
config.py - Scalper Configuration
All trading rules in one place. Aria never changes these automatically.
"""
import os

# Supported markets
VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]
KRAKEN_PAIRS  = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD"}
TRADINGVIEW_SYMBOLS = {
    "BTCUSD": "BITSTAMP:BTCUSD",
    "ETHUSD": "BITSTAMP:ETHUSD",
}

# Timeframe strategy
# 1H  -> trend filter
# 15m -> confirmation
# 5m  -> entry execution
TIMEFRAMES = {"1H": 60, "15m": 15, "5m": 5, "Daily": 1440}
TREND_TF    = "1H"
CONFIRM_TF  = "15m"
ENTRY_TF    = "5m"

# Risk management
RISK_PER_TRADE_PCT   = 2.0   # 2% per trade
DAILY_LOSS_LIMIT_PCT = 5.0   # 5% daily loss limit
MIN_RISK_REWARD      = 1.2   # Minimum R:R for scalping
SL_ATR_MULTIPLIER    = 0.5   # Tight stop: 0.5x ATR
TP_ATR_MULTIPLIER    = 1.0   # TP: 1x ATR

# Trade frequency
MAX_TRADES_PER_DAY    = 8
MAX_OPEN_POSITIONS    = 2
MIN_CONFIDENCE        = 70

# Entry rules
REQUIRE_STRUCTURE           = True
REQUIRE_EMA_ALIGNMENT       = True
REQUIRE_VOLUME_CONFIRMATION = True
REQUIRE_CANDLE_PATTERN      = False  # Replaced by candle strength score
REQUIRE_RSI_CONFIRMATION    = True

# Trade lifecycle (Scalper mode)
SCALPER_BREAKEVEN_USD  = 5.0   # Move SL to break-even at +$5
SCALPER_PARTIAL_TP_USD = 10.0  # Close 50% at +$10
SCALPER_TRAIL_USD      = 15.0  # Trail remainder after +$15
SCALPER_TIMEOUT_HOURS  = 4     # Close stuck trade after 4 hours
SCALPER_MIN_PROFIT_USD = 2.0   # Min profit needed after timeout

# Trend Runner mode (auto-activated on strong signals)
TREND_MIN_CONFIDENCE   = 85
TREND_MIN_STRENGTH     = 70
TREND_TRAIL_ATR_MULT   = 0.8  # Trail at price - (ATR * 0.8)

# Confidence scoring max points
CONFIDENCE_MAX = {
    "Market Structure": 25,
    "EMA Alignment":    25,
    "RSI":              15,
    "Candle Strength":  20,
    "Volume":           15,
}

# BUY / SELL rule sets
BUY_CONDITIONS  = {"market_structure": "Bullish", "ema_alignment": "Bullish",
                   "volume_confirm": True, "min_rr": MIN_RISK_REWARD,
                   "min_confidence": MIN_CONFIDENCE}
SELL_CONDITIONS = {"market_structure": "Bearish", "ema_alignment": "Bearish",
                   "volume_confirm": True, "min_rr": MIN_RISK_REWARD,
                   "min_confidence": MIN_CONFIDENCE}

# MT5 bridge (run mt5_bridge.py on your Windows PC)
MT5_BRIDGE_URL = os.getenv("MT5_BRIDGE_URL", "")
MT5_BRIDGE_KEY = os.getenv("MT5_BRIDGE_KEY", "aria-secret")
USE_MT5        = bool(MT5_BRIDGE_URL)

# Dashboard
DASHBOARD_REFRESH_SECONDS = 30
MIN_TRADES_FOR_RECS       = 5
