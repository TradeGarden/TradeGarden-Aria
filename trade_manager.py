"""
trade_manager.py — Stage 5: MANAGE
=====================================
Runs as a separate background service alongside main.py.

Start it with:
  python trade_manager.py

Responsibilities:
  - Watch open positions every 60 seconds
  - Move Stop Loss to Break Even when price moves 1 ATR in profit
  - Trail Stop Loss when price moves 2 ATR in profit (if enabled)
  - Auto-close when Stop Loss or Take Profit is hit
  - Detect reversal signals and warn or close early
  - Log every action to the journal
  - Send notifications (console log; extend for email/Telegram later)

This service runs independently of FastAPI.
It only reads/writes the shared position and balance files.
"""

import time
import json
import os
from datetime import datetime

from scanner import fetch_current_price, fetch_candles
from analyzer import calc_atr, detect_patterns, calc_market_structure
from executor import load_position, load_balance, save_balance, clear_position
from journal import append_trade

# ──────────────────────────────────────────────
#  CONFIG
# ──────────────────────────────────────────────

SCAN_INTERVAL_SECONDS = 60      # Check every 60 seconds
BREAK_EVEN_ATR_MULT   = 1.0     # Move SL to BE when price moves 1 ATR in profit
TRAILING_ATR_MULT     = 2.0     # Start trailing when price moves 2 ATR in profit
TRAILING_ENABLED      = True    # Set False to disable trailing stop
REVERSAL_CLOSE        = True    # Close early on strong reversal pattern


# ──────────────────────────────────────────────
#  NOTIFICATIONS
# ──────────────────────────────────────────────

def notify(message: str):
    """
    Notification gateway.
    Currently logs to console.
    Extend this to send Telegram / email / webhook alerts.
    """
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[ARIA NOTIFY] {timestamp} — {message}")
    # TODO: Add Telegram bot, email, or webhook here


# ──────────────────────────────────────────────
#  POSITION FILE UPDATE
# ──────────────────────────────────────────────

POSITION_FILE = "paper_position.json"


def update_position_file(position: dict):
    with open(POSITION_FILE, "w") as f:
        json.dump(position, f, indent=2)


# ──────────────────────────────────────────────
#  CLOSE TRADE
# ──────────────────────────────────────────────

def close_trade(position: dict, current_price: float, reason: str):
    """Close the trade, update balance, log to journal."""
    entry = position["entry_price"]
    size  = position["size"]
    side  = position["side"]

    pl = (current_price - entry) * size if side == "BUY" else (entry - current_price) * size
    pl = round(pl, 2)

    balance     = load_balance()
    new_balance = round(balance + pl, 2)
    save_balance(new_balance)
    clear_position()

    duration = ""
    try:
        opened   = datetime.fromisoformat(position["opened_at"])
        dur      = datetime.utcnow() - opened
        h, rem   = divmod(int(dur.total_seconds()), 3600)
        m        = rem // 60
        duration = f"{h}h {m}m"
    except Exception:
        pass

    append_trade({
        "action":      "CLOSE",
        "trade_id":    position.get("trade_id", ""),
        "symbol":      position["symbol"],
        "side":        side,
        "entry":       entry,
        "exit":        current_price,
        "stop_loss":   position["stop_loss"],
        "take_profit": position["take_profit"],
        "size":        size,
        "risk":        position.get("risk_amount", 0),
        "pl":          pl,
        "new_balance": new_balance,
        "duration":    duration,
        "exit_reason": reason,
        "closed_at":   datetime.utcnow().isoformat(),
    })

    notify(f"{position['symbol']} {side} CLOSED — P/L: ${pl:,.2f} | Reason: {reason} | New Balance: ${new_balance:,.2f}")
    return pl


# ──────────────────────────────────────────────
#  BREAK EVEN
# ──────────────────────────────────────────────

def move_to_break_even(position: dict, current_price: float, atr: float) -> dict:
    """Move SL to entry price when profit >= 1 ATR."""
    if position.get("be_moved"):
        return position
    entry  = position["entry_price"]
    side   = position["side"]
    profit = (current_price - entry) if side == "BUY" else (entry - current_price)

    if profit >= atr * BREAK_EVEN_ATR_MULT:
        position["stop_loss"] = entry
        position["be_moved"]  = True
        update_position_file(position)
        notify(f"{position['symbol']} Stop Loss moved to Break Even @ ${entry:,.2f}")
        append_trade({
            "action":    "SL_MOVED_BE",
            "trade_id":  position.get("trade_id", ""),
            "symbol":    position["symbol"],
            "new_sl":    entry,
            "timestamp": datetime.utcnow().isoformat(),
        })
    return position


# ──────────────────────────────────────────────
#  TRAILING STOP
# ──────────────────────────────────────────────

def trail_stop(position: dict, current_price: float, atr: float) -> dict:
    """Trail the SL behind price by 1 ATR once profit >= 2 ATR."""
    if not TRAILING_ENABLED:
        return position
    entry  = position["entry_price"]
    side   = position["side"]
    profit = (current_price - entry) if side == "BUY" else (entry - current_price)

    if profit < atr * TRAILING_ATR_MULT:
        return position

    if side == "BUY":
        new_sl = round(current_price - atr, 2)
        if new_sl > position["stop_loss"]:
            position["stop_loss"] = new_sl
            update_position_file(position)
            notify(f"{position['symbol']} Trailing SL moved to ${new_sl:,.2f}")
    else:
        new_sl = round(current_price + atr, 2)
        if new_sl < position["stop_loss"]:
            position["stop_loss"] = new_sl
            update_position_file(position)
            notify(f"{position['symbol']} Trailing SL moved to ${new_sl:,.2f}")

    return position


# ──────────────────────────────────────────────
#  REVERSAL DETECTION
# ──────────────────────────────────────────────

def check_reversal(position: dict, candles: list) -> bool:
    """
    Detect if a strong opposing candlestick pattern has formed.
    If REVERSAL_CLOSE is True, this triggers an early close.
    """
    if not REVERSAL_CLOSE or not candles:
        return False

    patterns = detect_patterns(candles)
    side     = position["side"]

    for p in patterns:
        if p["strength"] == "Strong":
            if side == "BUY"  and p["direction"] == "Bearish":
                notify(f"⚠️ Reversal detected on {position['symbol']} — {p['name']} (Short position open)")
                return True
            if side == "SELL" and p["direction"] == "Bullish":
                notify(f"⚠️ Reversal detected on {position['symbol']} — {p['name']} (Long position open)")
                return True
    return False


# ──────────────────────────────────────────────
#  MAIN MONITOR LOOP
# ──────────────────────────────────────────────

def monitor():
    """
    Main trade management loop.
    Runs every SCAN_INTERVAL_SECONDS seconds.
    """
    notify("Trade Manager started — monitoring every 60 seconds")

    while True:
        try:
            position = load_position()

            if not position:
                time.sleep(SCAN_INTERVAL_SECONDS)
                continue

            symbol        = position["symbol"]
            current_price = fetch_current_price(symbol)
            candles       = fetch_candles(symbol, 1440)
            atr           = calc_atr(candles, 14) if candles else 0

            sl   = position["stop_loss"]
            tp   = position["take_profit"]
            side = position["side"]

            # ── Check SL / TP ──
            if side == "BUY":
                if current_price <= sl:
                    close_trade(position, current_price, "Stop Loss hit")
                    time.sleep(SCAN_INTERVAL_SECONDS)
                    continue
                if current_price >= tp:
                    close_trade(position, current_price, "Take Profit reached")
                    time.sleep(SCAN_INTERVAL_SECONDS)
                    continue
            else:
                if current_price >= sl:
                    close_trade(position, current_price, "Stop Loss hit")
                    time.sleep(SCAN_INTERVAL_SECONDS)
                    continue
                if current_price <= tp:
                    close_trade(position, current_price, "Take Profit reached")
                    time.sleep(SCAN_INTERVAL_SECONDS)
                    continue

            # ── Reversal check ──
            if candles and check_reversal(position, candles):
                close_trade(position, current_price, "Reversal pattern detected")
                time.sleep(SCAN_INTERVAL_SECONDS)
                continue

            # ── Break Even ──
            if atr > 0:
                position = move_to_break_even(position, current_price, atr)

            # ── Trailing Stop ──
            if atr > 0:
                position = trail_stop(position, current_price, atr)

            # Log heartbeat
            pl = (current_price - position["entry_price"]) * position["size"]
            if side == "SELL":
                pl = (position["entry_price"] - current_price) * position["size"]
            print(f"[ARIA MONITOR] {symbol} {side} | Price: ${current_price:,.2f} | "
                  f"SL: ${sl:,.2f} | TP: ${tp:,.2f} | P/L: ${pl:,.2f}")

        except Exception as e:
            print(f"[ARIA ERROR] Trade manager error: {e}")

        time.sleep(SCAN_INTERVAL_SECONDS)


# ──────────────────────────────────────────────
#  ENTRY POINT
# ──────────────────────────────────────────────

if __name__ == "__main__":
    monitor()
