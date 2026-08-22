"""
config.py - Aria Trading Configuration
Professional rules. Quality over quantity.
Target: 3-8 good trades per day, not 100 random ones.
"""
import os

VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]
KRAKEN_PAIRS  = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD"}
TRADINGVIEW_SYMBOLS = {
    "BTCUSD": "BITSTAMP:BTCUSD",
    "ETHUSD": "BITSTAMP:ETHUSD",
}

# ── Risk Management ────────────────────────────────────────────────────────
RISK_PER_TRADE_PCT   = 1.0    # 1% per trade - safe
DAILY_LOSS_LIMIT_PCT = 3.0    # Stop if down 3% in a day
MIN_RISK_REWARD      = 1.5    # Minimum 1.5:1 R:R
SL_ATR_MULTIPLIER    = 1.5    # Stop loss at 1.5x ATR - room to breathe
TP_ATR_MULTIPLIER    = 2.5    # Take profit at 2.5x ATR

# ── Trade Frequency ────────────────────────────────────────────────────────
MAX_TRADES_PER_DAY = 6        # Max 6 quality trades per day
MAX_OPEN_POSITIONS = 2        # BTC + ETH simultaneously
MIN_CONFIDENCE     = 70       # Minimum 70% confidence to trade

# ── Entry Requirements ─────────────────────────────────────────────────────
MIN_TREND_STRENGTH       = 30  # Minimum trend strength %
MIN_TIMEFRAMES_ALIGNED   = 2   # At least 2 timeframes must agree
RSI_OVERBOUGHT           = 75  # Don't buy above this
RSI_OVERSOLD             = 25  # Don't sell below this

# ── Trade Management ───────────────────────────────────────────────────────
BREAKEVEN_USD    = 3.0    # Move SL to break-even at +$3
PARTIAL_TP_USD   = 6.0    # Close 50% at +$6
TRAIL_AFTER_USD  = 10.0   # Trail remainder after +$10
TIMEOUT_MINUTES  = 240    # Close if stuck > 4 hours (not 30 min)
MIN_PROFIT_USD   = 1.0    # Min profit needed to hold past timeout

# ── Scan Settings ──────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS    = 60
DASHBOARD_REFRESH_SECONDS = 30

# ── Confidence Scoring ─────────────────────────────────────────────────────
CONFIDENCE_MAX = {
    "Market Structure": 25,
    "EMA Alignment":    25,
    "RSI":              15,
    "Candle Strength":  20,
    "Volume":           15,
}

# ── Condition Sets ─────────────────────────────────────────────────────────
BUY_CONDITIONS  = {
    "market_structure": "Bullish",
    "ema_alignment":    "Bullish",
    "min_rr":           MIN_RISK_REWARD,
    "min_confidence":   MIN_CONFIDENCE,
}
SELL_CONDITIONS = {
    "market_structure": "Bearish",
    "ema_alignment":    "Bearish",
    "min_rr":           MIN_RISK_REWARD,
    "min_confidence":   MIN_CONFIDENCE,
}

# ── Intelligence (no API keys needed) ─────────────────────────────────────
WHALE_ALERT_KEY = os.getenv("WHALE_ALERT_KEY", "")
COINGLASS_KEY   = os.getenv("COINGLASS_KEY", "")

MIN_TRADES_FOR_RECS = 5
