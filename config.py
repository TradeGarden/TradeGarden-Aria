"""
config.py — Aria Final Professional Ruleset
============================================
R-based profit management. Structure-first entry.
Max risk $5 per trade. Quality over quantity.
7 trades maximum per day — ceiling, not target.
"""
import os

VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]
KRAKEN_PAIRS  = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD"}
TRADINGVIEW_SYMBOLS = {
    "BTCUSD": "BITSTAMP:BTCUSD",
    "ETHUSD": "BITSTAMP:ETHUSD",
}

# ── Risk (the foundation of everything) ───────────────────────────────────
MAX_RISK_USD         = 5.0    # Hard cap: never risk more than $5 per trade
RISK_PER_TRADE_PCT   = 1.0    # Also 1% of balance — use whichever is smaller
DAILY_LOSS_LIMIT_PCT = 5.0    # Pause trading if down 5% in a day

# ── R-Based Profit Milestones ──────────────────────────────────────────────
# 1R = actual initial risk on that trade (max $5)
MILESTONE_BREAKEVEN   = 1.0   # +1R  → move SL to entry (risk-free)
MILESTONE_LOCK        = 1.2   # +1.2R → move SL above entry, lock $2.50 min
MILESTONE_LOCK_AMOUNT = 0.5   # Lock this fraction of 1R as guaranteed profit
MILESTONE_PARTIAL_TP  = 2.0   # +2R  → close 50% of position
MILESTONE_TRAIL       = 2.0   # +2R  → activate ATR trailing stop on remainder
TRAIL_ATR_MULT        = 0.6   # Trail distance = ATR × 0.6

# ── Entry Requirements ─────────────────────────────────────────────────────
MIN_RISK_REWARD        = 1.8   # Minimum 1.8:1 R:R — never take worse
SL_ATR_MULTIPLIER      = 1.5   # SL = 1.5× ATR (adapts to volatility)
MIN_CONFIDENCE         = 70    # 70%+ confidence required
MIN_TREND_STRENGTH     = 30    # 30%+ directional strength
MIN_TIMEFRAMES_ALIGNED = 2     # At least 2 of 4 TFs must agree
RSI_OVERBOUGHT         = 75    # No BUY entries above this
RSI_OVERSOLD           = 25    # No SELL entries below this

# ── Working Timeframes ─────────────────────────────────────────────────────
# 15m → entry confirmation
# 1H  → setup confirmation
# 4H  → directional context
# 1D  → major structure/trend
TIMEFRAMES = ["15m", "1H", "4H", "Daily"]

# ── Trade Frequency ────────────────────────────────────────────────────────
MAX_TRADES_PER_DAY   = 7      # CEILING not target. 0 trades is fine.
MAX_OPEN_POSITIONS   = 2      # BTC + ETH simultaneously

# ── Dynamic Timeout (based on entry timeframe) ─────────────────────────────
# If structure still valid → hold regardless of time
# If structure broken → exit immediately regardless of time
TIMEOUT_SHORT_HOURS  = 4     # 15m / 1H setups
TIMEOUT_MEDIUM_HOURS = 12    # 1H / 4H setups
TIMEOUT_LONG_HOURS   = 24    # 4H / 1D setups
MAX_HOLD_DAYS        = 7     # Never hold longer than 7 days
MIN_PROFIT_TO_HOLD   = 0.5   # Must show $0.50+ profit to hold past timeout

# ── Scan Settings ──────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS     = 60
DASHBOARD_REFRESH_SECONDS = 30

# ── Confidence Scoring (max 100) ───────────────────────────────────────────
# Priority: Structure → Timeframe → Momentum → Indicators
CONFIDENCE_MAX = {
    "Market Structure": 25,   # HH/HL, BOS, CHoCH, S/R, FVG
    "EMA Alignment":    25,   # EMA confirmation of structure
    "RSI":              15,   # Momentum
    "Candle Strength":  20,   # Volume + candle pattern
    "Volume":           15,   # Volume confirms the move
}

BUY_CONDITIONS = {
    "market_structure": "Bullish",
    "min_rr":           MIN_RISK_REWARD,
    "min_confidence":   MIN_CONFIDENCE,
}
SELL_CONDITIONS = {
    "market_structure": "Bearish",
    "min_rr":           MIN_RISK_REWARD,
    "min_confidence":   MIN_CONFIDENCE,
}

WHALE_ALERT_KEY = os.getenv("WHALE_ALERT_KEY", "")
COINGLASS_KEY   = os.getenv("COINGLASS_KEY", "")
MIN_TRADES_FOR_RECS = 3
