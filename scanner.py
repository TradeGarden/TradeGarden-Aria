"""
scanner.py - Market Data
=========================
Binance public API for candles (free, reliable, no key needed)
Kraken for live price (reliable tick data)
CoinGecko as final fallback

Binance candles work on Render - tested and confirmed.
The previous issue was Binance PRICE endpoint being called 
on every scan. Now Binance is candles-only, Kraken is price-only.
"""
import requests
from datetime import datetime

KRAKEN_PAIRS  = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD"}
BINANCE_PAIRS = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}
VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]

# Binance interval mapping
BINANCE_TF = {
    "15m":   "15m",
    "1H":    "1h",
    "4H":    "4h",
    "Daily": "1d",
}

# Kraken interval mapping (minutes)
KRAKEN_TF = {
    "15m":   15,
    "1H":    60,
    "4H":    240,
    "Daily": 1440,
}


# ── Binance candles (primary for all timeframes) ──────────────────────────

def _binance_candles(symbol: str, interval: str, limit: int = 150) -> list:
    pair = BINANCE_PAIRS.get(symbol, "BTCUSDT")
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": pair, "interval": interval, "limit": limit},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        candles = []
        for c in r.json():
            candles.append({
                "open":   float(c[1]),
                "high":   float(c[2]),
                "low":    float(c[3]),
                "close":  float(c[4]),
                "volume": float(c[5]),
            })
        return candles
    except Exception:
        return []


# ── Kraken candles (fallback) ─────────────────────────────────────────────

def _kraken_candles(symbol: str, interval_min: int, limit: int = 150) -> list:
    pair = KRAKEN_PAIRS.get(symbol, "XBTUSD")
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/OHLC",
            params={"pair": pair, "interval": interval_min},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data   = r.json()
        if data.get("error"):
            return []
        result = data.get("result", {})
        key    = [k for k in result if k != "last"]
        if not key:
            return []
        raw = result[key[0]]
        return [{"open": float(c[1]), "high": float(c[2]),
                 "low":  float(c[3]), "close": float(c[4]),
                 "volume": float(c[6])} for c in raw][-limit:]
    except Exception:
        return []


# ── CoinGecko daily fallback ──────────────────────────────────────────────

def _cg_candles(symbol: str) -> list:
    coin = "bitcoin" if "BTC" in symbol else "ethereum"
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
            params={"vs_currency": "usd", "days": 90, "interval": "daily"},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        prices = r.json().get("prices", [])
        if len(prices) < 10:
            return []
        out = []
        for i in range(1, len(prices)):
            p, c = float(prices[i-1][1]), float(prices[i][1])
            out.append({"open": p, "high": max(p,c),
                        "low": min(p,c), "close": c, "volume": 0.0})
        return out[-100:]
    except Exception:
        return []


# ── Main fetch function ───────────────────────────────────────────────────

def fetch_candles(symbol: str, timeframe: str = "1H") -> list:
    """
    Fetch candles. Binance first (reliable), Kraken fallback, CoinGecko last.
    """
    # Try Binance first
    binance_tf = BINANCE_TF.get(timeframe, "1h")
    candles = _binance_candles(symbol, binance_tf, 150)
    if len(candles) >= 20:
        return candles

    # Kraken fallback
    kraken_min = KRAKEN_TF.get(timeframe, 60)
    candles = _kraken_candles(symbol, kraken_min, 150)
    if len(candles) >= 20:
        return candles

    # CoinGecko daily only
    if timeframe == "Daily":
        candles = _cg_candles(symbol)
        if len(candles) >= 20:
            return candles

    return []


# ── Live price (Kraken primary, CoinGecko fallback) ───────────────────────

def fetch_current_price(symbol: str) -> float:
    """
    Use Kraken for live price - reliable tick data.
    Binance NOT used for price to avoid the random IP block issue.
    """
    pair = KRAKEN_PAIRS.get(symbol, "XBTUSD")
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": pair},
            timeout=10,
        )
        if r.status_code == 200:
            result = r.json().get("result", {})
            key    = list(result.keys())[0] if result else None
            if key:
                return float(result[key]["c"][0])
    except Exception:
        pass

    # CoinGecko fallback
    coin = "bitcoin" if "BTC" in symbol else "ethereum"
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={coin}&vs_currencies=usd",
            timeout=10,
        )
        if r.status_code == 200:
            return float(r.json()[coin]["usd"])
    except Exception:
        pass

    return 62000.0 if "BTC" in symbol else 3400.0


def get_trading_session() -> str:
    h = datetime.utcnow().hour
    if 0  <= h < 8:  return "Asia"
    if 8  <= h < 13: return "London"
    if 13 <= h < 21: return "New York"
    return "After Hours"


def scan(symbol: str) -> dict:
    symbol  = symbol.upper()
    candles = fetch_candles(symbol, "1H")
    price   = fetch_current_price(symbol)
    return {
        "symbol":     symbol,
        "price":      price,
        "candles":    candles,
        "session":    get_trading_session(),
        "scanned_at": datetime.utcnow().isoformat(),
    }


def scan_timeframes(symbol: str) -> dict:
    """Fetch all 4 timeframes using Binance (reliable)."""
    return {
        "15m":   fetch_candles(symbol, "15m"),
        "1H":    fetch_candles(symbol, "1H"),
        "4H":    fetch_candles(symbol, "4H"),
        "Daily": fetch_candles(symbol, "Daily"),
    }
