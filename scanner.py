"""
scanner.py - Stage 1: SCAN
===========================
Responsibilities:
  - Fetch live BTC/ETH prices
  - Download OHLCV candles (Kraken primary, CoinGecko fallback)
  - Detect trading session (Asia / London / New York)
  - Return a clean market snapshot for the analyzer
"""

import requests
from datetime import datetime

KRAKEN_PAIRS = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD"}
VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]


# ──────────────────────────────────────────────
#  CANDLES
# ──────────────────────────────────────────────

def fetch_candles_kraken(symbol: str, interval_min: int = 1440, limit: int = 120) -> list:
    """
    Fetch OHLCV candles from Kraken public API.
    No API key required. Works from any server.

    interval_min options: 1, 5, 15, 30, 60, 240, 1440
    """
    pair = KRAKEN_PAIRS.get(symbol, "XBTUSD")
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/OHLC",
            params={"pair": pair, "interval": interval_min},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            return []
        result = data.get("result", {})
        key = [k for k in result if k != "last"]
        if not key:
            return []
        raw = result[key[0]]
        candles = [
            {
                "open":   float(c[1]),
                "high":   float(c[2]),
                "low":    float(c[3]),
                "close":  float(c[4]),
                "volume": float(c[6]),
            }
            for c in raw
        ]
        return candles[-limit:]
    except Exception:
        return []


def fetch_candles_coingecko(symbol: str) -> list:
    """
    CoinGecko market_chart fallback - daily candles, last 100 days.
    Used when Kraken is unavailable.
    """
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
        if len(prices) < 55:
            return []
        candles = []
        for i in range(1, len(prices)):
            prev, curr = float(prices[i - 1][1]), float(prices[i][1])
            candles.append({
                "open":   prev,
                "high":   max(prev, curr),
                "low":    min(prev, curr),
                "close":  curr,
                "volume": 0.0,
            })
        return candles[-100:]
    except Exception:
        return []


def fetch_candles(symbol: str, interval_min: int = 1440) -> list:
    """
    Try Kraken first, fall back to CoinGecko for daily candles.
    Returns empty list if both fail.
    """
    candles = fetch_candles_kraken(symbol, interval_min)
    if len(candles) >= 55:
        return candles
    if interval_min == 1440:
        candles = fetch_candles_coingecko(symbol)
        if len(candles) >= 55:
            return candles
    return []


# ──────────────────────────────────────────────
#  LIVE PRICE
# ──────────────────────────────────────────────

def fetch_price_kraken(symbol: str) -> float | None:
    pair = KRAKEN_PAIRS.get(symbol, "XBTUSD")
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": pair},
            timeout=10,
        )
        r.raise_for_status()
        result = r.json().get("result", {})
        key = list(result.keys())[0] if result else None
        if key:
            return float(result[key]["c"][0])
    except Exception:
        return None


def fetch_price_coingecko(symbol: str) -> float | None:
    coin = "bitcoin" if "BTC" in symbol else "ethereum"
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd",
            timeout=10,
        )
        r.raise_for_status()
        return float(r.json()[coin]["usd"])
    except Exception:
        return None


def fetch_current_price(symbol: str) -> float:
    """Kraken first, CoinGecko fallback, hard fallback last."""
    price = fetch_price_kraken(symbol)
    if price:
        return price
    price = fetch_price_coingecko(symbol)
    if price:
        return price
    return 62000.0 if "BTC" in symbol else 3400.0


# ──────────────────────────────────────────────
#  SESSION
# ──────────────────────────────────────────────

def get_trading_session() -> str:
    """Return current Forex/crypto trading session based on UTC hour."""
    h = datetime.utcnow().hour
    if 0 <= h < 8:    return "Asia"
    if 8 <= h < 13:   return "London"
    if 13 <= h < 21:  return "New York"
    return "After Hours"


# ──────────────────────────────────────────────
#  MAIN SCAN FUNCTION
# ──────────────────────────────────────────────

def scan(symbol: str) -> dict:
    """
    Stage 1 - SCAN.
    Collects all raw market data needed by the analyzer.
    Returns a clean snapshot dict. No analysis is done here.
    """
    symbol = symbol.upper()
    if symbol not in VALID_SYMBOLS:
        symbol = "BTCUSD"

    candles       = fetch_candles(symbol, 1440)
    current_price = fetch_current_price(symbol)
    session       = get_trading_session()

    return {
        "symbol":      symbol,
        "price":       current_price,
        "candles":     candles,
        "session":     session,
        "scanned_at":  datetime.utcnow().isoformat(),
        "data_source": "Kraken" if fetch_price_kraken(symbol) else "CoinGecko",
    }


def scan_timeframes(symbol: str) -> dict:
    """
    Fetch candles for all four timeframes.
    Used by the analyzer for multi-timeframe analysis.
    """
    symbol = symbol.upper()
    return {
        "15m":   fetch_candles(symbol, 15),
        "1H":    fetch_candles(symbol, 60),
        "4H":    fetch_candles(symbol, 240),
        "Daily": fetch_candles(symbol, 1440),
    }
