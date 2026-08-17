"""
scanner.py - Market Data
Kraken primary (reliable on Render), CoinGecko fallback.
Binance removed - it blocks Render IPs randomly causing crashes.
"""
import requests
from datetime import datetime

KRAKEN_PAIRS = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD"}
VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]

KRAKEN_TF = {"1m": 1, "5m": 5, "15m": 15, "1H": 60, "4H": 240, "Daily": 1440}


def _kraken_candles(symbol: str, interval_min: int, limit: int = 200) -> list:
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
                 "low":  float(c[3]), "close": float(c[4]),
                 "volume": float(c[6])} for c in raw][-limit:]
    except Exception:
        return []


def _cg_candles(symbol: str) -> list:
    coin = "bitcoin" if "BTC" in symbol else "ethereum"
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
            params={"vs_currency": "usd", "days": 60, "interval": "daily"},
            timeout=15,
        )
        r.raise_for_status()
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


def fetch_candles(symbol: str, timeframe: str = "1H") -> list:
    mins = KRAKEN_TF.get(timeframe, 60)
    c = _kraken_candles(symbol, mins)
    if len(c) >= 10:
        return c
    if timeframe == "Daily":
        return _cg_candles(symbol)
    return []


def fetch_current_price(symbol: str) -> float:
    pair = KRAKEN_PAIRS.get(symbol, "XBTUSD")
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": pair}, timeout=10,
        )
        r.raise_for_status()
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
        return float(r.json()[coin]["usd"])
    except Exception:
        return 62000.0 if "BTC" in symbol else 3400.0


def get_trading_session() -> str:
    h = datetime.utcnow().hour
    if 0  <= h < 8:  return "Asia"
    if 8  <= h < 13: return "London"
    if 13 <= h < 21: return "New York"
    return "After Hours"


def scan(symbol: str) -> dict:
    symbol  = symbol.upper()
    price   = fetch_current_price(symbol)
    candles = fetch_candles(symbol, "1H")
    return {
        "symbol":     symbol,
        "price":      price,
        "candles":    candles,
        "session":    get_trading_session(),
        "scanned_at": datetime.utcnow().isoformat(),
    }


def scan_timeframes(symbol: str) -> dict:
    return {
        "15m":   fetch_candles(symbol, "15m"),
        "1H":    fetch_candles(symbol, "1H"),
        "Daily": fetch_candles(symbol, "Daily"),
    }
EOF
echo "scanner.py done"
