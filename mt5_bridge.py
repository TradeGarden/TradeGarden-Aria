"""
mt5_bridge.py - MT5/Exness Bridge with auto-reconnect
=======================================================
Run on your Windows PC where MT5 is installed.
Handles disconnections gracefully - auto-reconnects on every request.

Setup:
  pip install MetaTrader5 flask
  set MT5_LOGIN=your_account_number
  set MT5_PASSWORD=your_password
  set MT5_SERVER=Exness-MT5Trial9
  python mt5_bridge.py
"""

import os
import time
from datetime import datetime, timedelta
from flask import Flask, jsonify, request

app = Flask(__name__)

MT5_LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER   = os.getenv("MT5_SERVER", "Exness-MT5Trial9")
BRIDGE_KEY   = os.getenv("BRIDGE_KEY", "aria-secret")

SYMBOL_MAP = {
    "BTCUSD": "BTCUSDm",
    "ETHUSD": "ETHUSDm",
}

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    print("ERROR: Run: pip install MetaTrader5")


# ── Auto-reconnect on every request ──────────────────────────────────────

def ensure_connected() -> tuple:
    """
    Try to connect to MT5 before every request.
    Returns (True, info) or (False, error_message).
    Retries up to 3 times with a short delay.
    """
    if not MT5_AVAILABLE:
        return False, "MetaTrader5 not installed"

    for attempt in range(3):
        try:
            # Always initialize fresh
            if not mt5.initialize():
                error = mt5.last_error()
                print(f"[Bridge] MT5 init failed: {error}")
                time.sleep(1)
                continue

            # Login if credentials provided
            if MT5_LOGIN and MT5_PASSWORD:
                logged = mt5.login(
                    MT5_LOGIN,
                    password=MT5_PASSWORD,
                    server=MT5_SERVER
                )
                if not logged:
                    error = mt5.last_error()
                    print(f"[Bridge] Login failed (attempt {attempt+1}): {error}")
                    mt5.shutdown()
                    time.sleep(2)
                    continue

            info = mt5.account_info()
            if info:
                print(f"[Bridge] Connected: {info.login} | "
                      f"Balance: {info.balance} {info.currency}")
                return True, info

        except Exception as e:
            print(f"[Bridge] Connection error (attempt {attempt+1}): {e}")
            time.sleep(1)

    return False, "Could not connect to MT5. Check MT5 is open and logged in."


def require_key(f):
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Bridge-Key") != BRIDGE_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper


# ── Routes ────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    if not MT5_AVAILABLE:
        return jsonify({
            "status":  "error",
            "message": "MetaTrader5 not installed. Run: pip install MetaTrader5"
        })

    connected, result = ensure_connected()
    if not connected:
        return jsonify({
            "status":  "disconnected",
            "message": result,
            "fix":     "Open MT5, log into Exness, enable AutoTrading (F7)"
        })

    info = mt5.account_info()
    return jsonify({
        "status":   "connected",
        "account":  info.login,
        "balance":  info.balance,
        "equity":   info.equity,
        "currency": info.currency,
        "server":   info.server,
        "demo":     info.trade_mode == 0,
    })


@app.route("/account")
@require_key
def account():
    connected, result = ensure_connected()
    if not connected:
        return jsonify({"error": result}), 503

    info = mt5.account_info()
    if not info:
        return jsonify({"error": "No account info"}), 500

    return jsonify({
        "login":       info.login,
        "balance":     info.balance,
        "equity":      info.equity,
        "margin":      info.margin,
        "free_margin": info.margin_free,
        "profit":      info.profit,
        "currency":    info.currency,
        "leverage":    info.leverage,
        "server":      info.server,
        "demo":        info.trade_mode == 0,
    })


@app.route("/price/<symbol>")
@require_key
def get_price(symbol):
    connected, result = ensure_connected()
    if not connected:
        return jsonify({"error": result}), 503

    mt5_sym = SYMBOL_MAP.get(symbol, symbol)

    # Make sure symbol is visible
    if not mt5.symbol_select(mt5_sym, True):
        return jsonify({"error": f"Cannot select {mt5_sym}"}), 404

    tick = mt5.symbol_info_tick(mt5_sym)
    if not tick:
        return jsonify({"error": f"No tick for {mt5_sym}"}), 404

    return jsonify({
        "symbol": symbol,
        "bid":    tick.bid,
        "ask":    tick.ask,
        "spread": round(tick.ask - tick.bid, 2),
        "time":   datetime.utcfromtimestamp(tick.time).isoformat(),
    })


@app.route("/open", methods=["POST"])
@require_key
def open_position():
    connected, result = ensure_connected()
    if not connected:
        return jsonify({"error": result, "success": False}), 503

    data    = request.json
    symbol  = data.get("symbol", "BTCUSD")
    side    = data.get("side", "BUY")
    size    = float(data.get("size", 0.01))
    sl      = float(data.get("stop_loss", 0))
    tp      = float(data.get("take_profit", 0))
    comment = data.get("comment", "Aria")

    mt5_sym = SYMBOL_MAP.get(symbol, symbol)

    # Select symbol
    if not mt5.symbol_select(mt5_sym, True):
        return jsonify({"success": False,
                        "error": f"Symbol {mt5_sym} not available"}), 400

    # Get symbol info for lot validation
    sym_info = mt5.symbol_info(mt5_sym)
    if not sym_info:
        return jsonify({"success": False, "error": "No symbol info"}), 400

    # Validate and clamp lot size
    min_lot  = sym_info.volume_min
    max_lot  = sym_info.volume_max
    lot_step = sym_info.volume_step
    size     = max(min_lot, min(max_lot, round(round(size/lot_step)*lot_step, 8)))

    tick = mt5.symbol_info_tick(mt5_sym)
    if not tick:
        return jsonify({"success": False, "error": "No price"}), 400

    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    price      = tick.ask if side == "BUY" else tick.bid

    req = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       mt5_sym,
        "volume":       size,
        "type":         order_type,
        "price":        price,
        "sl":           sl,
        "tp":           tp,
        "deviation":    50,
        "magic":        20250101,
        "comment":      comment,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(req)

    if result is None:
        error = mt5.last_error()
        return jsonify({"success": False,
                        "error": f"Order send failed: {error}"}), 400

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return jsonify({
            "success": False,
            "error":   result.comment,
            "retcode": result.retcode,
        }), 400

    print(f"[Bridge] OPENED: {side} {size} {mt5_sym} @ {result.price} "
          f"| Ticket #{result.order}")

    return jsonify({
        "success": True,
        "ticket":  result.order,
        "price":   result.price,
        "volume":  result.volume,
        "symbol":  symbol,
        "side":    side,
    })


@app.route("/close", methods=["POST"])
@require_key
def close_position():
    connected, result = ensure_connected()
    if not connected:
        return jsonify({"error": result, "success": False}), 503

    data   = request.json
    ticket = int(data.get("ticket", 0))
    symbol = data.get("symbol", "BTCUSD")
    side   = data.get("side", "BUY")
    size   = float(data.get("size", 0.01))

    mt5_sym    = SYMBOL_MAP.get(symbol, symbol)
    tick       = mt5.symbol_info_tick(mt5_sym)
    if not tick:
        return jsonify({"success": False, "error": "No price"}), 400

    close_type = mt5.ORDER_TYPE_SELL if side == "BUY" else mt5.ORDER_TYPE_BUY
    price      = tick.bid if side == "BUY" else tick.ask

    result = mt5.order_send({
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       mt5_sym,
        "volume":       size,
        "type":         close_type,
        "position":     ticket,
        "price":        price,
        "deviation":    50,
        "magic":        20250101,
        "comment":      "Aria Close",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    })

    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        error = result.comment if result else str(mt5.last_error())
        return jsonify({"success": False, "error": error}), 400

    print(f"[Bridge] CLOSED ticket #{ticket} @ {result.price}")
    return jsonify({"success": True, "ticket": ticket, "price": result.price})


@app.route("/positions")
@require_key
def positions():
    connected, result = ensure_connected()
    if not connected:
        return jsonify({"error": result}), 503

    pos = mt5.positions_get() or []
    return jsonify({"positions": [{
        "ticket":     p.ticket,
        "symbol":     p.symbol,
        "side":       "BUY" if p.type == 0 else "SELL",
        "volume":     p.volume,
        "open_price": p.price_open,
        "current":    p.price_current,
        "sl":         p.sl,
        "tp":         p.tp,
        "profit":     p.profit,
        "time":       datetime.utcfromtimestamp(p.time).isoformat(),
    } for p in pos]})


@app.route("/history")
@require_key
def history():
    connected, result = ensure_connected()
    if not connected:
        return jsonify({"error": result}), 503

    from_date = datetime.utcnow() - timedelta(days=30)
    deals = mt5.history_deals_get(from_date, datetime.utcnow()) or []
    return jsonify({"history": [{
        "ticket": d.ticket,
        "symbol": d.symbol,
        "type":   d.type,
        "volume": d.volume,
        "price":  d.price,
        "profit": d.profit,
        "time":   datetime.utcfromtimestamp(d.time).isoformat(),
    } for d in list(deals)[-50:]]})


# ── Startup ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("Aria MT5 Bridge")
    print("=" * 50)
    print(f"Account : {MT5_LOGIN}")
    print(f"Server  : {MT5_SERVER}")
    print(f"Port    : 5555")
    print()

    if not MT5_AVAILABLE:
        print("ERROR: MetaTrader5 not installed")
        print("Run: pip install MetaTrader5")
    else:
        connected, result = ensure_connected()
        if connected:
            info = mt5.account_info()
            print(f"Connected!")
            print(f"Balance : {info.balance} {info.currency}")
            print(f"Equity  : {info.equity} {info.currency}")
            print(f"Mode    : {'Demo' if info.trade_mode == 0 else 'Live'}")
        else:
            print(f"Not connected: {result}")
            print()
            print("Fix:")
            print("1. Open MT5")
            print("2. Log into Exness")
            print("3. Press F7, enable AutoTrading")
            print("4. Restart this script")

    print()
    print("Bridge running at http://0.0.0.0:5555")
    print("Ctrl+C to stop")
    print("=" * 50)

    app.run(host="0.0.0.0", port=5555, debug=False)
