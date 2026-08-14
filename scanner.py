"""
scanner.py - Stage 1: Market Data
===================================
Primary source: Binance public API (free, no key, reliable for 5m/15m/1H)
Fallback: Kraken, then CoinGecko

Supports real scalping timeframes: 5m, 15m, 1H, Daily
Scan interval: every 5 minutes (not 60 seconds)
"""

import requests
from datetime import datetime

# Binance symbol mapping
BINANCE_PAIRS = {
    "BTCUSD": "BTCUSDT",
    "ETHUSD": "ETHUSDT",
}

# Kraken fallback
KRAKEN_PAIRS = {
    "BTCUSD": "XBTUSD",
    "ETHUSD": "ETHUSD",
}

VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]


# ── Binance candles (primary) ─────────────────────────────────────────────

def fetch_candles_binance(symbol: str, interval: str = "1h",
                          limit: int = 100) -> list:
    """
    Fetch OHLCV candles from Binance public API.
    No API key required.
    interval options: 1m, 5m, 15m, 30m, 1h, 4h, 1d
    """
    pair = BINANCE_PAIRS.get(symbol, "BTCUSDT")
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": pair, "interval": interval, "limit": limit},
            timeout=15,
        )
        r.raise_for_status()
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


def fetch_price_binance(symbol: str) -> float:
    pair = BINANCE_PAIRS.get(symbol, "BTCUSDT")
    try:
        r = requests.get(
            f"https://api.binance.com/api/v3/ticker/price",
            params={"symbol": pair},
            timeout=10,
        )
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception:
        return 0.0


# ── Kraken fallback ───────────────────────────────────────────────────────

KRAKEN_INTERVALS = {"5m": 5, "15m": 15, "1H": 60, "4H": 240, "Daily": 1440}

def fetch_candles_kraken(symbol: str, interval_min: int = 60,
                         limit: int = 100) -> list:
    pair = KRAKEN_PAIRS.get(symbol, "XBTUSD")
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/OHLC",
            params={"pair": pair, "interval": interval_min},
            timeout=15,
        )
        r.raise_for_status()
        data   = r.json()
        if data.get("error"):
            return []
        result = data.get("result", {})
        key    = [k for k in result if k != "last"]
        if not key:
            return []
        raw = result[key[0]]
        return [{"open": float(c[1]), "high": float(c[2]),
                 "low": float(c[3]), "close": float(c[4]),
                 "volume": float(c[6])} for c in raw][-limit:]
    except Exception:
        return []


def fetch_price_kraken(symbol: str) -> float:
    pair = KRAKEN_PAIRS.get(symbol, "XBTUSD")
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": pair},
            timeout=10,
        )
        r.raise_for_status()
        result = r.json().get("result", {})
        key    = list(result.keys())[0] if result else None
        if key:
            return float(result[key]["c"][0])
    except Exception:
        pass
    return 0.0


# ── CoinGecko fallback (daily only) ──────────────────────────────────────

def fetch_candles_coingecko(symbol: str) -> list:
    coin = "bitcoin" if "BTC" in symbol else "ethereum"
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
            params={"vs_currency": "usd", "days": 100, "interval": "daily"},
            timeout=15,
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        prices = r.json().get("prices", [])
        if len(prices) < 20:
            return []
        candles = []
        for i in range(1, len(prices)):
            p, c = float(prices[i-1][1]), float(prices[i][1])
            candles.append({"open": p, "high": max(p,c),
                            "low": min(p,c), "close": c, "volume": 0.0})
        return candles[-100:]
    except Exception:
        return []


# ── Main fetch functions ──────────────────────────────────────────────────

# Map timeframe label to Binance interval string
TF_TO_BINANCE = {
    "5m":    "5m",
    "15m":   "15m",
    "1H":    "1h",
    "4H":    "4h",
    "Daily": "1d",
}

TF_TO_KRAKEN_MIN = {
    "5m":    5,
    "15m":   15,
    "1H":    60,
    "4H":    240,
    "Daily": 1440,
}


def fetch_candles(symbol: str, timeframe: str = "1H") -> list:
    """
    Fetch candles for given timeframe.
    Tries Binance first (most reliable), then Kraken, then CoinGecko (daily only).
    """
    # Try Binance
    binance_interval = TF_TO_BINANCE.get(timeframe, "1h")
    candles = fetch_candles_binance(symbol, binance_interval, 150)
    if len(candles) >= 20:
        return candles

    # Try Kraken
    kraken_min = TF_TO_KRAKEN_MIN.get(timeframe, 60)
    candles = fetch_candles_kraken(symbol, kraken_min, 150)
    if len(candles) >= 20:
        return candles

    # CoinGecko (daily only fallback)
    if timeframe == "Daily":
        candles = fetch_candles_coingecko(symbol)
        if len(candles) >= 20:
            return candles

    return []


def fetch_current_price(symbol: str) -> float:
    """Binance first, Kraken fallback."""
    price = fetch_price_binance(symbol)
    if price > 0:
        return price
    price = fetch_price_kraken(symbol)
    if price > 0:
        return price
    return 62000.0 if "BTC" in symbol else 3400.0


def get_trading_session() -> str:
    h = datetime.utcnow().hour
    if 0 <= h < 8:   return "Asia"
    if 8 <= h < 13:  return "London"
    if 13 <= h < 21: return "New York"
    return "After Hours"


def scan(symbol: str) -> dict:
    """
    Stage 1 - SCAN.
    Uses 1H candles for main analysis.
    Returns clean market snapshot.
    """
    symbol  = symbol.upper()
    candles = fetch_candles(symbol, "1H")
    price   = fetch_current_price(symbol)

    return {
        "symbol":      symbol,
        "price":       price,
        "candles":     candles,
        "session":     get_trading_session(),
        "scanned_at":  datetime.utcnow().isoformat(),
        "source":      "Binance" if fetch_price_binance(symbol) > 0 else "Kraken",
    }


def scan_timeframes(symbol: str) -> dict:
    """Fetch all timeframes for multi-TF analysis."""
    return {
        "5m":    fetch_candles(symbol, "5m"),
        "15m":   fetch_candles(symbol, "15m"),
        "1H":    fetch_candles(symbol, "1H"),
        "Daily": fetch_candles(symbol, "Daily"),
    }
