""""""
config.py — System Configuration
==================================
All trading rules, risk limits, and system settings live here.
Aria never changes these automatically.
Only the user changes them.
"""

# ──────────────────────────────────────────────
#  SUPPORTED MARKETS
# ──────────────────────────────────────────────

VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]

KRAKEN_PAIRS = {
    "BTCUSD": "XBTUSD",
    "ETHUSD": "ETHUSD",
}

TRADINGVIEW_SYMBOLS = {
    "BTCUSD": "BITSTAMP:BTCUSD",
    "ETHUSD": "BITSTAMP:ETHUSD",
}

# ──────────────────────────────────────────────
#  TIMEFRAME STRATEGY
#  Daily  → overall trend filter
#  4H     → trend confirmation
#  1H     → setup confirmation
#  15m    → entry execution
# ──────────────────────────────────────────────

TIMEFRAMES = {
    "Daily": 1440,
    "4H":    240,
    "1H":    60,
    "15m":   15,
}

EXECUTION_TIMEFRAME = "15m"   # Final entry signal comes from here

# ──────────────────────────────────────────────
#  RISK MANAGEMENT
# ──────────────────────────────────────────────

RISK_PER_TRADE_PCT  = 1.0     # Base risk 1% of balance per trade
# Confidence-based scaling applied on top:
#   60-69% confidence → 20% of risk (test position)
#   70-84% confidence → 50% of risk (half position)
#   85%+   confidence → 100% of risk (full position)
DAILY_LOSS_LIMIT_PCT= 3.0     # Stop trading if daily loss hits 3%
MIN_RISK_REWARD     = 2.0     # Minimum R:R ratio (1:2)
SL_ATR_MULTIPLIER   = 1.5     # Stop loss = entry ± (ATR × 1.5)
TP_ATR_MULTIPLIER   = 3.0     # Take profit = entry ± (ATR × 3.0)

# ──────────────────────────────────────────────
#  TRADE FREQUENCY
# ──────────────────────────────────────────────

MIN_TRADES_PER_DAY  = 0       # Aria is fine taking 0 trades
TARGET_TRADES_PER_DAY = 2     # Ideal range
MAX_TRADES_PER_DAY  = 6       # Hard limit — stop after this
MAX_OPEN_POSITIONS  = 3       # Max 3 open trades at once (BTC + ETH + scale-in)

# ──────────────────────────────────────────────
#  DECISION RULES
#  All conditions must be met to trigger BUY or SELL.
#  If any condition is missing → WAIT.
# ──────────────────────────────────────────────

MIN_CONFIDENCE      = 60      # Minimum confidence score to trade (0–100)

BUY_CONDITIONS = {
    "market_structure": "Bullish",   # HH / HL required
    "ema_alignment":    "Bullish",   # EMA20 above EMA50
    "volume_confirm":   True,        # Volume must confirm the move
    "candle_confirm":   True,        # Bullish confirmation candle required
    "min_rr":           MIN_RISK_REWARD,
    "min_confidence":   MIN_CONFIDENCE,
}

SELL_CONDITIONS = {
    "market_structure": "Bearish",   # LH / LL required
    "ema_alignment":    "Bearish",   # EMA20 below EMA50
    "volume_confirm":   True,        # Volume must confirm the move
    "candle_confirm":   True,        # Bearish confirmation candle required
    "min_rr":           MIN_RISK_REWARD,
    "min_confidence":   MIN_CONFIDENCE,
}

# ──────────────────────────────────────────────
#  TRADE MANAGER (Stage 5)
# ──────────────────────────────────────────────

SCAN_INTERVAL_SECONDS  = 60      # How often trade_manager checks price
BREAK_EVEN_ATR_MULT    = 1.0     # Move SL to BE when profit >= 1 ATR
TRAILING_ENABLED       = True    # Enable trailing stop loss
TRAILING_ATR_MULT      = 2.0     # Start trailing when profit >= 2 ATR
REVERSAL_CLOSE_ENABLED = True    # Close early on strong reversal candle

# ──────────────────────────────────────────────
#  AUTO-SCAN (dashboard refresh)
# ──────────────────────────────────────────────

DASHBOARD_REFRESH_SECONDS = 60   # Page auto-refreshes every 60s

# ──────────────────────────────────────────────
#  REPORTS
# ──────────────────────────────────────────────

WEEKLY_REPORT_DAY    = "Monday"  # Auto-generate weekly report on this day
MIN_TRADES_FOR_RECS  = 5        # Minimum trades before recommendations appear

config.py — System Configuration
==================================
All trading rules, risk limits, and system settings live here.
Aria never changes these automatically.
Only the user changes them.
"""

# ──────────────────────────────────────────────
#  SUPPORTED MARKETS
# ──────────────────────────────────────────────

VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]

KRAKEN_PAIRS = {
    "BTCUSD": "XBTUSD",
    "ETHUSD": "ETHUSD",
}

TRADINGVIEW_SYMBOLS = {
    "BTCUSD": "BITSTAMP:BTCUSD",
    "ETHUSD": "BITSTAMP:ETHUSD",
}

# ──────────────────────────────────────────────
#  TIMEFRAME STRATEGY
#  Daily  → overall trend filter
#  4H     → trend confirmation
#  1H     → setup confirmation
#  15m    → entry execution
# ──────────────────────────────────────────────

TIMEFRAMES = {
    "Daily": 1440,
    "4H":    240,
    "1H":    60,
    "15m":   15,
}

EXECUTION_TIMEFRAME = "15m"   # Final entry signal comes from here

# ──────────────────────────────────────────────
#  RISK MANAGEMENT
# ──────────────────────────────────────────────

RISK_PER_TRADE_PCT  = 1.0     # Base risk 1% of balance per trade
# Confidence-based scaling applied on top:
#   60-69% confidence → 20% of risk (test position)
#   70-84% confidence → 50% of risk (half position)
#   85%+   confidence → 100% of risk (full position)
DAILY_LOSS_LIMIT_PCT= 3.0     # Stop trading if daily loss hits 3%
MIN_RISK_REWARD     = 2.0     # Minimum R:R ratio (1:2)
SL_ATR_MULTIPLIER   = 1.5     # Stop loss = entry ± (ATR × 1.5)
TP_ATR_MULTIPLIER   = 3.0     # Take profit = entry ± (ATR × 3.0)

# ──────────────────────────────────────────────
#  TRADE FREQUENCY
# ──────────────────────────────────────────────

MIN_TRADES_PER_DAY  = 0       # Aria is fine taking 0 trades
TARGET_TRADES_PER_DAY = 2     # Ideal range
MAX_TRADES_PER_DAY  = 6       # Hard limit — stop after this
MAX_OPEN_POSITIONS  = 2       # Max 2 open trades at a time

# ──────────────────────────────────────────────
#  DECISION RULES
#  All conditions must be met to trigger BUY or SELL.
#  If any condition is missing → WAIT.
# ──────────────────────────────────────────────

MIN_CONFIDENCE      = 60      # Minimum confidence score to trade (0–100)

BUY_CONDITIONS = {
    "market_structure": "Bullish",   # HH / HL required
    "ema_alignment":    "Bullish",   # EMA20 above EMA50
    "volume_confirm":   True,        # Volume must confirm the move
    "candle_confirm":   True,        # Bullish confirmation candle required
    "min_rr":           MIN_RISK_REWARD,
    "min_confidence":   MIN_CONFIDENCE,
}

SELL_CONDITIONS = {
    "market_structure": "Bearish",   # LH / LL required
    "ema_alignment":    "Bearish",   # EMA20 below EMA50
    "volume_confirm":   True,        # Volume must confirm the move
    "candle_confirm":   True,        # Bearish confirmation candle required
    "min_rr":           MIN_RISK_REWARD,
    "min_confidence":   MIN_CONFIDENCE,
}

# ──────────────────────────────────────────────
#  TRADE MANAGER (Stage 5)
# ──────────────────────────────────────────────

SCAN_INTERVAL_SECONDS  = 60      # How often trade_manager checks price
BREAK_EVEN_ATR_MULT    = 1.0     # Move SL to BE when profit >= 1 ATR
TRAILING_ENABLED       = True    # Enable trailing stop loss
TRAILING_ATR_MULT      = 2.0     # Start trailing when profit >= 2 ATR
REVERSAL_CLOSE_ENABLED = True    # Close early on strong reversal candle

# ──────────────────────────────────────────────
#  AUTO-SCAN (dashboard refresh)
# ──────────────────────────────────────────────

DASHBOARD_REFRESH_SECONDS = 60   # Page auto-refreshes every 60s

# ──────────────────────────────────────────────
#  REPORTS
# ──────────────────────────────────────────────

WEEKLY_REPORT_DAY    = "Monday"  # Auto-generate weekly report on this day
MIN_TRADES_FOR_RECS  = 5        # Minimum trades before recommendations appear
