"""
config.py — Aria Professional Trading Rules
============================================
All values are derived from real market data.
Nothing is hardcoded or estimated.

Position sizing philosophy:
  - Risk is based on actual ATR stop distance
  - Stronger setups get larger position sizes
  - Winners are allowed to run — no forced exits
  - Max $5 risk per trade but targets can be $15-30+
"""
import os

VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]
KRAKEN_PAIRS  = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD"}
TRADINGVIEW_SYMBOLS = {
    "BTCUSD": "BITSTAMP:BTCUSD",
    "ETHUSD": "BITSTAMP:ETHUSD",
}

# ── Risk Management ────────────────────────────────────────────
MAX_RISK_USD         = 5.0    # Max $5 risk per trade (1R)
RISK_PER_TRADE_PCT   = 1.0    # 1% of balance
DAILY_LOSS_LIMIT_PCT = 5.0    # Stop if down 5% today

# ── Entry Quality ──────────────────────────────────────────────
MIN_RISK_REWARD        = 1.8   # Minimum 1.8:1 R:R
SL_ATR_MULTIPLIER      = 1.5   # Stop = 1.5x ATR below/above entry
TP_ATR_MULTIPLIER      = 3.5   # Initial TP = 3.5x ATR (allows bigger moves)
MIN_CONFIDENCE         = 70    # 70%+ confidence required
MIN_TREND_STRENGTH     = 15    # 15%+ trend strength (real swing-based)
MIN_TIMEFRAMES_ALIGNED = 2     # 2+ timeframes must agree
RSI_OVERBOUGHT         = 80    # No BUY above 80 (real Wilder RSI)
RSI_OVERSOLD           = 20    # No SELL below 20

# ── Trade Frequency ────────────────────────────────────────────
MAX_TRADES_PER_DAY   = 7      # Ceiling not target
MAX_OPEN_POSITIONS   = 2      # BTC + ETH simultaneously

# ── R-Based Milestone System ───────────────────────────────────
# 1R = actual initial risk on that trade (max $5)
# Targets are in R multiples — adapts to any position size

MILESTONE_BREAKEVEN   = 1.0   # +1R → SL to entry (risk free)
MILESTONE_LOCK        = 1.2   # +1.2R → SL above entry, lock profit
MILESTONE_LOCK_AMOUNT = 0.5   # Lock 50% of 1R as guaranteed profit
MILESTONE_PARTIAL_TP  = 2.0   # +2R → close 50% of position
MILESTONE_TRAIL       = 2.0   # +2R → activate ATR trail on rest
TRAIL_ATR_MULT        = 0.8   # Trail = price - (ATR × 0.8) — not too tight

# Extended runner: if market continues past 3R, hold the rest
# Let structure decide the exit, not a fixed dollar amount

# ── Dynamic Timeout (by timeframe) ────────────────────────────
TIMEOUT_SHORT_HOURS  = 4     # 15m/1H entries
TIMEOUT_MEDIUM_HOURS = 12    # 1H/4H entries
TIMEOUT_LONG_HOURS   = 24    # 4H/Daily entries
MAX_HOLD_DAYS        = 7     # Never hold beyond 7 days
MIN_PROFIT_TO_HOLD   = 0.50  # Must show $0.50+ to hold past timeout

# ── Scan Settings ──────────────────────────────────────────────
SCAN_INTERVAL_SECONDS     = 60
DASHBOARD_REFRESH_SECONDS = 30

# ── Confluence Scoring (max 100) ───────────────────────────────
# Grouped by category — avoids double counting
CONFIDENCE_MAX = {
    "Market Structure": 25,  # trend, BOS, displacement
    "EMA Alignment":    25,  # EMA direction + slope
    "RSI":              15,  # momentum
    "Candle Strength":  20,  # volume + pattern
    "Volume":           15,  # relative volume confirmation
}

# ── API Keys (optional) ────────────────────────────────────────
WHALE_ALERT_KEY = os.getenv("WHALE_ALERT_KEY", "")
COINGLASS_KEY   = os.getenv("COINGLASS_KEY", "")

MIN_TRADES_FOR_RECS = 3
