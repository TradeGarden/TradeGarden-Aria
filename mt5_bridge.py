"""
mt5_bridge.py - MT5/Exness Bridge
===================================
Run this on your WINDOWS PC where MT5 is installed.

Setup:
  1. pip install MetaTrader5 flask
  2. Open MT5 and log into your Exness account
  3. python mt5_bridge.py
  4. Set MT5_BRIDGE_URL=http://YOUR_PC_IP:5555 in Render environment

Your PC must stay on while trading.
Works with Exness demo or live accounts.
"""

import os
from datetime import datetime, timedelta
from flask import Flask, jsonify, request

app = Flask(__name__)

MT5_LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER   = os.getenv("MT5_SERVER", "Exness-MT5Trial")
BRIDGE_KEY   = os.getenv("BRIDGE_KEY", "aria-secret")

# Exness symbol names
SYMBOL_MAP = {
    "BTCUSD": "BTCUSDm",
    "ETHUSD": "ETHUSDm",
}

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    print("WARNING: MetaTrader5 not installed. Run: pip install MetaTrader5")


def require_key(f):
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Bridge-Key") != BRIDGE_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper


def connect():
    if not MT5_AVAILABLE:
        return False
    if not mt5.initialize():
        return False
    if MT5_LOGIN and MT5_PASSWORD:
        return mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    return True


@app.route("/health")
def health():
    if not MT5_AVAILABLE:
        return jsonify({"status": "mt5_not_installed",
                        "message": "pip install MetaTrader5"})
    connected = connect()
    info = mt5.account_info() if connected else None
    return jsonify({
        "status":   "connected" if connected else "disconnected",
        "account":  info.login   if info else None,
        "balance":  info.balance  if info else None,
        "equity":   info.equity   if info else None,
        "currency": info.currency if info else None,
        "server":   info.server   if info else None,
    })


@app.route("/account")
@require_key
def account():
    if not connect():
        return jsonify({"error": "MT5 not connected"}), 500
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
    })


@app.route("/price/<symbol>")
@require_key
def get_price(symbol):
    if not connect():
        return jsonify({"error": "MT5 not connected"}), 500
    mt5_sym = SYMBOL_MAP.get(symbol, symbol)
    tick = mt5.symbol_info_tick(mt5_sym)
    if not tick:
        return jsonify({"error": f"Symbol {mt5_sym} not found"}), 404
    return jsonify({
        "symbol": symbol,
        "bid":    tick.bid,
        "ask":    tick.ask,
        "spread": round(tick.ask - tick.bid, 2),
    })


@app.route("/open", methods=["POST"])
@require_key
def open_position():
    if not connect():
        return jsonify({"error": "MT5 not connected"}), 500

    data      = request.json
    symbol    = data.get("symbol", "BTCUSD")
    side      = data.get("side", "BUY")
    size      = float(data.get("size", 0.01))
    sl        = float(data.get("stop_loss", 0))
    tp        = float(data.get("take_profit", 0))
    comment   = data.get("comment", "Aria")

    mt5_sym   = SYMBOL_MAP.get(symbol, symbol)
    tick      = mt5.symbol_info_tick(mt5_sym)
    if not tick:
        return jsonify({"error": f"No price for {mt5_sym}"}), 400

    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    price      = tick.ask if side == "BUY" else tick.bid

    result = mt5.order_send({
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       mt5_sym,
        "volume":       size,
        "type":         order_type,
        "price":        price,
        "sl":           sl,
        "tp":           tp,
        "deviation":    30,
        "magic":        20250101,
        "comment":      comment,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    })

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return jsonify({"success": False, "error": result.comment,
                        "retcode": result.retcode}), 400

    return jsonify({"success": True, "ticket": result.order,
                    "price": result.price, "volume": result.volume,
                    "symbol": symbol, "side": side})


@app.route("/close", methods=["POST"])
@require_key
def close_position():
    if not connect():
        return jsonify({"error": "MT5 not connected"}), 500

    data   = request.json
    ticket = int(data.get("ticket", 0))
    symbol = data.get("symbol", "BTCUSD")
    side   = data.get("side", "BUY")
    size   = float(data.get("size", 0.01))

    mt5_sym    = SYMBOL_MAP.get(symbol, symbol)
    tick       = mt5.symbol_info_tick(mt5_sym)
    if not tick:
        return jsonify({"error": "No price"}), 400

    close_type = mt5.ORDER_TYPE_SELL if side == "BUY" else mt5.ORDER_TYPE_BUY
    price      = tick.bid if side == "BUY" else tick.ask

    result = mt5.order_send({
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       mt5_sym,
        "volume":       size,
        "type":         close_type,
        "position":     ticket,
        "price":        price,
        "deviation":    30,
        "magic":        20250101,
        "comment":      "Aria Close",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    })

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return jsonify({"success": False, "error": result.comment}), 400

    return jsonify({"success": True, "ticket": ticket, "price": result.price})


@app.route("/positions")
@require_key
def positions():
    if not connect():
        return jsonify({"error": "MT5 not connected"}), 500
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
    if not connect():
        return jsonify({"error": "MT5 not connected"}), 500
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


if __name__ == "__main__":
    print("Aria MT5 Bridge starting...")
    print(f"Account: {MT5_LOGIN} | Server: {MT5_SERVER}")
    if MT5_AVAILABLE and connect():
        info = mt5.account_info()
        if info:
            print(f"Connected! Balance: {info.balance} {info.currency}")
    else:
        print("MT5 not connected - start MT5 terminal first")
    app.run(host="0.0.0.0", port=5555, debug=False)
