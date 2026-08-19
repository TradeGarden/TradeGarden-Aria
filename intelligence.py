"""
intelligence.py - Aria Intelligence Engine
==========================================
All free, no API keys required.

Provides:
  1. Crypto news (BTC/ETH specific) - CryptoPanic
  2. Fear & Greed index - Alternative.me
  3. Funding rates (BTC/ETH) - Coinglass public API
  4. Open interest - Coinglass public API
  5. Economic calendar highlights - Tradingeconomics RSS
  6. DXY/correlation signal - derived from price data

Used by decision_engine.py to add context to trade decisions.
"""

import requests
from datetime import datetime, timezone

TIMEOUT = 10   # seconds per request


# ══════════════════════════════════════════════
#  1. FEAR & GREED INDEX
#  Source: alternative.me (free, no key)
# ══════════════════════════════════════════════

def get_fear_greed() -> dict:
    """
    Returns current Fear & Greed index.
    0-25  = Extreme Fear (good to BUY)
    26-45 = Fear
    46-55 = Neutral
    56-75 = Greed
    76-100 = Extreme Greed (good to SELL / take profit)
    """
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data  = r.json()["data"][0]
        value = int(data["value"])
        label = data["value_classification"]
        return {
            "value":     value,
            "label":     label,
            "signal":    _fg_signal(value),
            "timestamp": data.get("timestamp", ""),
        }
    except Exception:
        return {"value": 50, "label": "Neutral", "signal": "NEUTRAL", "timestamp": ""}


def _fg_signal(value: int) -> str:
    if value <= 25: return "EXTREME_FEAR"      # Historically good to buy
    if value <= 45: return "FEAR"
    if value <= 55: return "NEUTRAL"
    if value <= 75: return "GREED"
    return "EXTREME_GREED"                      # Historically good to sell


# ══════════════════════════════════════════════
#  2. CRYPTO NEWS (BTC/ETH specific)
#  Source: CryptoPanic free API + RSS fallback
# ══════════════════════════════════════════════

def get_crypto_news(symbol: str = "BTC", limit: int = 5) -> list:
    """
    Get latest news for BTC or ETH.
    Returns list of {title, url, sentiment, published_at, importance}
    """
    coin = "BTC" if "BTC" in symbol else "ETH"

    # Try CryptoPanic free endpoint
    try:
        r = requests.get(
            "https://cryptopanic.com/api/free/v1/posts/",
            params={
                "auth_token": "free",
                "currencies":  coin,
                "kind":        "news",
                "filter":      "important",
                "public":      "true",
            },
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            posts = r.json().get("results", [])[:limit]
            return [{
                "title":        p.get("title", ""),
                "url":          p.get("url", ""),
                "sentiment":    _news_sentiment(p),
                "published_at": p.get("published_at", "")[:16],
                "importance":   "HIGH" if p.get("is_important") else "NORMAL",
                "source":       p.get("source", {}).get("title", ""),
            } for p in posts]
    except Exception:
        pass

    # Fallback: CoinDesk RSS via rss2json
    try:
        feed = "https://www.coindesk.com/arc/outboundfeeds/rss/"
        r = requests.get(
            "https://api.rss2json.com/v1/api.json",
            params={"rss_url": feed, "count": limit},
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            items = r.json().get("items", [])[:limit]
            return [{
                "title":        i.get("title", ""),
                "url":          i.get("link", ""),
                "sentiment":    "NEUTRAL",
                "published_at": i.get("pubDate", "")[:16],
                "importance":   "NORMAL",
                "source":       "CoinDesk",
            } for i in items if coin in i.get("title","").upper()
              or coin in i.get("description","").upper()]
    except Exception:
        pass

    return []


def _news_sentiment(post: dict) -> str:
    votes = post.get("votes", {})
    bull  = votes.get("positive", 0) + votes.get("lol", 0)
    bear  = votes.get("negative", 0) + votes.get("dislike", 0)
    if bull > bear * 1.5: return "BULLISH"
    if bear > bull * 1.5: return "BEARISH"
    return "NEUTRAL"


# ══════════════════════════════════════════════
#  3. FUNDING RATES
#  Source: Coinglass public API (no key needed)
# ══════════════════════════════════════════════

def get_funding_rate(symbol: str = "BTCUSD") -> dict:
    """
    Perpetual futures funding rate.
    Positive = longs paying shorts (bearish pressure)
    Negative = shorts paying longs (bullish pressure)
    Extreme = potential reversal
    """
    coin = "BTC" if "BTC" in symbol else "ETH"
    try:
        r = requests.get(
            "https://open-api.coinglass.com/public/v2/funding",
            params={"symbol": coin},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        if data:
            # Get Binance rate as reference
            for exchange in data:
                if exchange.get("exchangeName") == "Binance":
                    rate = float(exchange.get("rate", 0))
                    return {
                        "rate":    round(rate * 100, 4),
                        "signal":  _funding_signal(rate),
                        "annualized": round(rate * 3 * 365 * 100, 1),
                    }
            # Fallback: first exchange
            rate = float(data[0].get("rate", 0))
            return {
                "rate":    round(rate * 100, 4),
                "signal":  _funding_signal(rate),
                "annualized": round(rate * 3 * 365 * 100, 1),
            }
    except Exception:
        pass
    return {"rate": 0.0, "signal": "NEUTRAL", "annualized": 0.0}


def _funding_signal(rate: float) -> str:
    if rate > 0.05:   return "OVERHEATED_LONGS"   # Longs very crowded
    if rate > 0.01:   return "LONGS_DOMINANT"
    if rate < -0.05:  return "OVERHEATED_SHORTS"  # Shorts very crowded
    if rate < -0.01:  return "SHORTS_DOMINANT"
    return "NEUTRAL"


# ══════════════════════════════════════════════
#  4. OPEN INTEREST
#  Source: Coinglass
# ══════════════════════════════════════════════

def get_open_interest(symbol: str = "BTCUSD") -> dict:
    coin = "BTC" if "BTC" in symbol else "ETH"
    try:
        r = requests.get(
            "https://open-api.coinglass.com/public/v2/open_interest",
            params={"symbol": coin},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        oi   = float(data.get("openInterest", 0))
        chg  = float(data.get("openInterestChange24h", 0))
        return {
            "open_interest":     round(oi / 1e9, 2),   # billions
            "change_24h_pct":    round(chg, 2),
            "signal":            _oi_signal(chg),
        }
    except Exception:
        return {"open_interest": 0, "change_24h_pct": 0, "signal": "NEUTRAL"}


def _oi_signal(change_pct: float) -> str:
    if change_pct > 5:   return "STRONG_EXPANSION"   # Strong momentum
    if change_pct > 2:   return "EXPANDING"
    if change_pct < -5:  return "STRONG_CONTRACTION"  # Potential reversal
    if change_pct < -2:  return "CONTRACTING"
    return "STABLE"


# ══════════════════════════════════════════════
#  5. ECONOMIC CALENDAR (high impact events)
#  Source: Tradingeconomics RSS (free)
# ══════════════════════════════════════════════

def get_economic_events() -> list:
    """
    Get today's high-impact economic events.
    These affect BTC/ETH especially: CPI, FOMC, NFP, GDP.
    """
    high_impact = ["CPI","FOMC","NFP","GDP","PPI","FOMC","INTEREST RATE",
                   "FED","POWELL","INFLATION","UNEMPLOYMENT","PAYROLL"]
    try:
        r = requests.get(
            "https://api.rss2json.com/v1/api.json",
            params={"rss_url": "https://tradingeconomics.com/rss/news.aspx",
                    "count": 20},
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            items = r.json().get("items", [])
            events = []
            for item in items:
                title = item.get("title","").upper()
                if any(k in title for k in high_impact):
                    events.append({
                        "title":      item.get("title",""),
                        "url":        item.get("link",""),
                        "published":  item.get("pubDate","")[:16],
                        "impact":     "HIGH",
                    })
            return events[:5]
    except Exception:
        pass
    return []


# ══════════════════════════════════════════════
#  6. FULL INTELLIGENCE SNAPSHOT
#  Call once per scan cycle
# ══════════════════════════════════════════════

def get_intelligence(symbol: str = "BTCUSD") -> dict:
    """
    Returns full intelligence snapshot for a symbol.
    Used by decision_engine.py to add context.
    Safe — if any source fails, returns neutral defaults.
    """
    fg      = get_fear_greed()
    news    = get_crypto_news(symbol, limit=5)
    funding = get_funding_rate(symbol)
    oi      = get_open_interest(symbol)
    events  = get_economic_events()

    # Overall intelligence signal
    signals = []
    if fg["signal"] in ("EXTREME_FEAR",):      signals.append("BULLISH")
    if fg["signal"] in ("EXTREME_GREED",):     signals.append("BEARISH")
    if funding["signal"] == "OVERHEATED_LONGS": signals.append("BEARISH")
    if funding["signal"] == "OVERHEATED_SHORTS":signals.append("BULLISH")

    bull_news = sum(1 for n in news if n["sentiment"] == "BULLISH")
    bear_news = sum(1 for n in news if n["sentiment"] == "BEARISH")
    if bull_news > bear_news: signals.append("BULLISH")
    if bear_news > bull_news: signals.append("BEARISH")

    bull_count = signals.count("BULLISH")
    bear_count = signals.count("BEARISH")

    if bull_count > bear_count:   overall = "BULLISH"
    elif bear_count > bull_count: overall = "BEARISH"
    else:                         overall = "NEUTRAL"

    # High impact event warning
    has_high_impact = len(events) > 0

    return {
        "symbol":           symbol,
        "fear_greed":       fg,
        "news":             news,
        "funding":          funding,
        "open_interest":    oi,
        "economic_events":  events,
        "overall_signal":   overall,
        "high_impact_event":has_high_impact,
        "timestamp":        datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════
#  INTELLIGENCE SCORE (0-20 bonus points)
#  Added to confidence when intelligence aligns
# ══════════════════════════════════════════════

def intelligence_score(intel: dict, side: str) -> int:
    """
    Returns 0-20 bonus confidence points from intelligence.
    Only adds to confidence — never blocks a trade by itself.
    """
    if not intel:
        return 0

    score = 0
    is_buy  = side == "BUY"
    is_sell = side == "SELL"

    # Fear & Greed (max 6pts)
    fg = intel.get("fear_greed", {}).get("signal", "NEUTRAL")
    if is_buy  and fg == "EXTREME_FEAR":   score += 6
    elif is_buy  and fg == "FEAR":         score += 3
    elif is_sell and fg == "EXTREME_GREED": score += 6
    elif is_sell and fg == "GREED":        score += 3

    # Funding rate (max 6pts)
    fs = intel.get("funding", {}).get("signal", "NEUTRAL")
    if is_buy  and fs == "OVERHEATED_SHORTS": score += 6
    elif is_buy  and fs == "SHORTS_DOMINANT": score += 3
    elif is_sell and fs == "OVERHEATED_LONGS": score += 6
    elif is_sell and fs == "LONGS_DOMINANT":   score += 3

    # News sentiment (max 5pts)
    news = intel.get("news", [])
    bull = sum(1 for n in news if n["sentiment"] == "BULLISH")
    bear = sum(1 for n in news if n["sentiment"] == "BEARISH")
    if is_buy  and bull > bear: score += min(5, bull * 2)
    if is_sell and bear > bull: score += min(5, bear * 2)

    # Open interest expansion (max 3pts)
    oi_sig = intel.get("open_interest", {}).get("signal", "NEUTRAL")
    if oi_sig in ("STRONG_EXPANSION", "EXPANDING"):
        score += 3

    return min(score, 20)
