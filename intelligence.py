"""
intelligence.py - Aria Intelligence Engine
==========================================
Fixed data sources with proper headers, fallbacks, and API keys.

Free API keys needed (sign up takes 2 minutes):
  WHALE_ALERT_KEY  - whale-alert.io/pricing (free tier)
  COINGLASS_KEY    - coinglass.com/pricing (free tier)

Without keys: CoinGecko, CryptoPanic, RSS feeds still work.
"""

import os
import requests
from datetime import datetime, timezone

# ── API Keys (set in Render environment variables) ────────────────────────
WHALE_ALERT_KEY = os.getenv("WHALE_ALERT_KEY", "")
COINGLASS_KEY   = os.getenv("COINGLASS_KEY", "")

# ── Standard headers (prevents most blocks) ───────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept":     "application/json",
}

T = 12  # timeout


def _get(url, params=None, headers=None, key_header=None, key_val=None):
    h = {**HEADERS}
    if headers:
        h.update(headers)
    if key_header and key_val:
        h[key_header] = key_val
    try:
        r = requests.get(url, params=params, headers=h, timeout=T)
        if r.status_code == 429:
            return {}  # rate limited
        if r.status_code == 403:
            return {}  # blocked
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def _rss(url, count=25):
    try:
        r = requests.get(
            "https://api.rss2json.com/v1/api.json",
            params={"rss_url": url, "count": count},
            headers=HEADERS, timeout=T,
        )
        if r.status_code == 200:
            return r.json().get("items", [])
    except Exception:
        pass
    return []


# ══════════════════════════════════════════════
#  1. FEAR & GREED
#  alternative.me - always works, no key needed
# ══════════════════════════════════════════════

def get_fear_greed() -> dict:
    try:
        data  = _get("https://api.alternative.me/fng/?limit=2")
        items = data.get("data", [])
        if items:
            cur  = items[0]
            val  = int(cur["value"])
            prev = int(items[1]["value"]) if len(items) > 1 else val
            return {
                "value":    val,
                "label":    cur["value_classification"],
                "previous": prev,
                "change":   val - prev,
                "signal":   ("EXTREME_FEAR"  if val <= 25 else
                             "FEAR"          if val <= 45 else
                             "NEUTRAL"       if val <= 55 else
                             "GREED"         if val <= 75 else
                             "EXTREME_GREED"),
                "available": True,
            }
    except Exception:
        pass
    return {"value": 50, "label": "N/A", "previous": 50,
            "change": 0, "signal": "NEUTRAL", "available": False}


# ══════════════════════════════════════════════
#  2. GLOBAL MARKETS
#  CoinGecko - free, no key needed
# ══════════════════════════════════════════════

def get_global_markets() -> dict:
    out = {
        "btc_dominance": 0, "eth_dominance": 0,
        "total_mcap_usd": 0, "mcap_change_24h": 0,
        "dxy_price": 0, "dxy_change": 0, "dxy_available": False,
        "available": False,
    }
    try:
        data = _get("https://api.coingecko.com/api/v3/global")
        d    = data.get("data", {})
        if d:
            out["btc_dominance"]   = round(d.get("market_cap_percentage", {}).get("btc", 0), 1)
            out["eth_dominance"]   = round(d.get("market_cap_percentage", {}).get("eth", 0), 1)
            out["total_mcap_usd"]  = round(d.get("total_market_cap", {}).get("usd", 0) / 1e12, 2)
            out["mcap_change_24h"] = round(d.get("market_cap_change_percentage_24h_usd", 0), 2)
            out["available"]       = True
    except Exception:
        pass

    # DXY from stooq - parse correctly
    try:
        r = requests.get(
            "https://stooq.com/q/l/?s=dxy&f=sd2t2ohlcv&h&e=csv",
            headers=HEADERS, timeout=T,
        )
        lines = [l for l in r.text.strip().split("\n") if l.strip()]
        if len(lines) > 1:
            parts = lines[-1].split(",")
            if len(parts) >= 6:
                try:
                    price = float(parts[4])  # close
                    open_ = float(parts[2])  # open
                    # Sanity check - DXY is always between 70 and 130
                    if 70 < price < 130:
                        out["dxy_price"]     = round(price, 3)
                        out["dxy_change"]    = round(price - open_, 3)
                        out["dxy_available"] = True
                except ValueError:
                    pass
    except Exception:
        pass

    return out


# ══════════════════════════════════════════════
#  3. BTC/ETH PRICE & 24H CHANGE
#  CoinGecko - free
# ══════════════════════════════════════════════

def get_coin_data(symbol: str = "BTCUSD") -> dict:
    coin = "bitcoin" if "BTC" in symbol else "ethereum"
    try:
        data = _get(
            f"https://api.coingecko.com/api/v3/coins/{coin}",
            params={"localization": "false", "tickers": "false",
                    "market_data": "true", "community_data": "false"},
        )
        md = data.get("market_data", {})
        return {
            "price":        md.get("current_price", {}).get("usd", 0),
            "change_24h":   round(md.get("price_change_percentage_24h", 0), 2),
            "volume_24h":   round(md.get("total_volume", {}).get("usd", 0) / 1e9, 2),
            "high_24h":     md.get("high_24h", {}).get("usd", 0),
            "low_24h":      md.get("low_24h", {}).get("usd", 0),
            "market_cap":   round(md.get("market_cap", {}).get("usd", 0) / 1e9, 2),
            "ath":          md.get("ath", {}).get("usd", 0),
            "ath_change":   round(md.get("ath_change_percentage", {}).get("usd", 0), 1),
            "available":    True,
        }
    except Exception:
        return {"price": 0, "change_24h": 0, "volume_24h": 0,
                "high_24h": 0, "low_24h": 0, "market_cap": 0,
                "ath": 0, "ath_change": 0, "available": False}


# ══════════════════════════════════════════════
#  4. CRYPTO NEWS
#  Multiple sources for maximum coverage
# ══════════════════════════════════════════════

def get_crypto_news(symbol: str = "BTCUSD", limit: int = 30) -> list:
    coin     = "BTC" if "BTC" in symbol else "ETH"
    coin_full= "bitcoin" if coin == "BTC" else "ethereum"
    news     = []

    # Source 1: CryptoPanic (best crypto news, free)
    try:
        r = requests.get(
            "https://cryptopanic.com/api/free/v1/posts/",
            params={"auth_token": "free", "currencies": coin,
                    "kind": "news", "public": "true", "filter": "hot"},
            headers=HEADERS, timeout=T,
        )
        if r.status_code == 200:
            for p in r.json().get("results", [])[:15]:
                votes = p.get("votes", {})
                bull  = votes.get("positive", 0) + votes.get("lol", 0)
                bear  = votes.get("negative", 0) + votes.get("dislike", 0)
                sent  = ("BULLISH" if bull > bear * 1.2
                         else "BEARISH" if bear > bull * 1.2
                         else "NEUTRAL")
                news.append({
                    "title":     p.get("title", ""),
                    "url":       p.get("url", ""),
                    "source":    p.get("source", {}).get("title", ""),
                    "coin":      coin,
                    "sentiment": sent,
                    "important": bool(p.get("is_important")),
                    "published": p.get("published_at", "")[:16],
                })
    except Exception:
        pass

    # Source 2: CoinDesk RSS
    if len(news) < 5:
        for item in _rss("https://www.coindesk.com/arc/outboundfeeds/rss/", 20):
            title = item.get("title", "")
            desc  = item.get("description", "").lower()
            if coin.lower() in title.lower() or coin_full in title.lower() \
               or coin_full in desc:
                news.append({
                    "title":     title,
                    "url":       item.get("link", ""),
                    "source":    "CoinDesk",
                    "coin":      coin,
                    "sentiment": "NEUTRAL",
                    "important": False,
                    "published": item.get("pubDate", "")[:16],
                })

    # Source 3: CoinTelegraph RSS
    if len(news) < 8:
        for item in _rss("https://cointelegraph.com/rss", 20):
            title = item.get("title", "")
            if coin.lower() in title.lower() or coin_full in title.lower():
                news.append({
                    "title":     title,
                    "url":       item.get("link", ""),
                    "source":    "CoinTelegraph",
                    "coin":      coin,
                    "sentiment": "NEUTRAL",
                    "important": False,
                    "published": item.get("pubDate", "")[:16],
                })

    # Source 4: Decrypt RSS
    if len(news) < 10:
        for item in _rss("https://decrypt.co/feed", 15):
            title = item.get("title", "")
            if coin.lower() in title.lower() or coin_full in title.lower():
                news.append({
                    "title":     title,
                    "url":       item.get("link", ""),
                    "source":    "Decrypt",
                    "coin":      coin,
                    "sentiment": "NEUTRAL",
                    "important": False,
                    "published": item.get("pubDate", "")[:16],
                })

    # Source 5: BeInCrypto RSS
    if len(news) < 12:
        for item in _rss("https://beincrypto.com/feed/", 15):
            title = item.get("title", "")
            if coin.lower() in title.lower() or coin_full in title.lower():
                news.append({
                    "title":     title,
                    "url":       item.get("link", ""),
                    "source":    "BeInCrypto",
                    "coin":      coin,
                    "sentiment": "NEUTRAL",
                    "important": False,
                    "published": item.get("pubDate", "")[:16],
                })

    # Deduplicate
    seen, unique = set(), []
    for n in news:
        key = n["title"][:50]
        if key and key not in seen:
            seen.add(key)
            unique.append(n)

    return unique[:limit]


# ══════════════════════════════════════════════
#  5. WHALE TRANSACTIONS
#  Whale Alert free API + RSS
# ══════════════════════════════════════════════

def get_whale_transactions(symbol: str = "BTCUSD") -> list:
    coin   = "BTC" if "BTC" in symbol else "ETH"
    whales = []

    # Try Whale Alert API (free key at whale-alert.io)
    if WHALE_ALERT_KEY:
        try:
            r = requests.get(
                "https://api.whale-alert.io/v1/transactions",
                params={"api_key": WHALE_ALERT_KEY, "min_value": 1000000,
                        "currency": coin.lower(), "limit": 20},
                headers=HEADERS, timeout=T,
            )
            if r.status_code == 200:
                for tx in r.json().get("transactions", []):
                    from_lbl = tx.get("from", {}).get("owner_type", "unknown")
                    to_lbl   = tx.get("to",   {}).get("owner_type", "unknown")
                    amt      = tx.get("amount", 0)
                    usd      = tx.get("amount_usd", 0)
                    from_nm  = tx.get("from", {}).get("owner", from_lbl)
                    to_nm    = tx.get("to",   {}).get("owner", to_lbl)

                    direction = (
                        "TO_EXCHANGE"    if to_lbl   == "exchange" else
                        "FROM_EXCHANGE"  if from_lbl == "exchange" else
                        "WALLET_TO_WALLET"
                    )
                    signal = ("BEARISH" if direction == "TO_EXCHANGE"
                              else "BULLISH" if direction == "FROM_EXCHANGE"
                              else "NEUTRAL")

                    whales.append({
                        "title":     f"{amt:,.0f} #{coin} (${usd/1e6:.1f}M USD) "
                                     f"transferred from {from_nm} to {to_nm}",
                        "url":       "",
                        "coin":      coin,
                        "amount":    amt,
                        "usd":       usd,
                        "direction": direction,
                        "signal":    signal,
                        "published": datetime.utcfromtimestamp(
                            tx.get("timestamp", 0)).strftime("%Y-%m-%d %H:%M"),
                    })
                if whales:
                    return whales[:15]
        except Exception:
            pass

    # RSS fallback (works without key)
    for item in _rss("https://whale-alert.io/rss/all", 30):
        title = item.get("title", "")
        if not title:
            continue
        tu = title.upper()
        if coin not in tu and \
           ("BITCOIN" not in tu or coin != "BTC") and \
           ("ETHEREUM" not in tu or coin != "ETH"):
            continue

        tl = title.lower()
        direction = (
            "TO_EXCHANGE"    if any(x in tl for x in
                ["to coinbase","to binance","to kraken","to okx",
                 "to bybit","to huobi","to exchange","to bitfinex"]) else
            "FROM_EXCHANGE"  if any(x in tl for x in
                ["from coinbase","from binance","from kraken","from okx",
                 "from bybit","from huobi","from exchange","from bitfinex"]) else
            "WALLET_TO_WALLET"
        )
        signal = ("BEARISH" if direction == "TO_EXCHANGE"
                  else "BULLISH" if direction == "FROM_EXCHANGE"
                  else "NEUTRAL")

        whales.append({
            "title":     title,
            "url":       item.get("link", ""),
            "coin":      coin,
            "amount":    0,
            "usd":       0,
            "direction": direction,
            "signal":    signal,
            "published": item.get("pubDate", "")[:16],
        })

    return whales[:15]


# ══════════════════════════════════════════════
#  6. FUNDING RATES
#  Coinglass (try with key, fallback without)
# ══════════════════════════════════════════════

def get_funding_rates(symbol: str = "BTCUSD") -> dict:
    coin = "BTC" if "BTC" in symbol else "ETH"

    # Try with API key
    headers_cg = {}
    if COINGLASS_KEY:
        headers_cg = {"coinglassSecret": COINGLASS_KEY}

    try:
        data = _get(
            "https://open-api.coinglass.com/public/v2/funding",
            params={"symbol": coin},
            headers=headers_cg,
        )
        items = data.get("data", [])
        if items:
            rates  = {}
            total  = 0.0
            count  = 0
            for ex in items:
                rate = float(ex.get("rate", 0))
                name = ex.get("exchangeName", "?")
                rates[name] = round(rate * 100, 4)
                total += rate
                count += 1
            avg = total / count if count else 0
            return {
                "average":    round(avg * 100, 4),
                "annualized": round(avg * 3 * 365 * 100, 1),
                "exchanges":  rates,
                "signal": ("OVERHEATED_LONGS"  if avg >  0.0005 else
                           "LONGS_DOMINANT"    if avg >  0.0001 else
                           "OVERHEATED_SHORTS" if avg < -0.0005 else
                           "SHORTS_DOMINANT"   if avg < -0.0001 else
                           "NEUTRAL"),
                "available": True,
            }
    except Exception:
        pass

    # Fallback: try Binance funding rate directly (free)
    try:
        pair = "BTCUSDT" if coin == "BTC" else "ETHUSDT"
        r = requests.get(
            f"https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": pair},
            headers=HEADERS, timeout=T,
        )
        if r.status_code == 200:
            d    = r.json()
            rate = float(d.get("lastFundingRate", 0))
            return {
                "average":    round(rate * 100, 4),
                "annualized": round(rate * 3 * 365 * 100, 1),
                "exchanges":  {"Binance": round(rate * 100, 4)},
                "signal": ("OVERHEATED_LONGS"  if rate >  0.0005 else
                           "LONGS_DOMINANT"    if rate >  0.0001 else
                           "OVERHEATED_SHORTS" if rate < -0.0005 else
                           "SHORTS_DOMINANT"   if rate < -0.0001 else
                           "NEUTRAL"),
                "available": True,
            }
    except Exception:
        pass

    return {"average": 0, "annualized": 0, "exchanges": {},
            "signal": "NEUTRAL", "available": False}


# ══════════════════════════════════════════════
#  7. OPEN INTEREST
#  Coinglass or Binance fallback
# ══════════════════════════════════════════════

def get_open_interest(symbol: str = "BTCUSD") -> dict:
    coin = "BTC" if "BTC" in symbol else "ETH"

    headers_cg = {"coinglassSecret": COINGLASS_KEY} if COINGLASS_KEY else {}

    try:
        data = _get(
            "https://open-api.coinglass.com/public/v2/open_interest",
            params={"symbol": coin},
            headers=headers_cg,
        )
        d = data.get("data", {})
        if d:
            oi  = float(d.get("openInterest", 0))
            chg = float(d.get("openInterestChange24h", 0))
            return {
                "value_usd":  round(oi / 1e9, 2),
                "change_24h": round(chg, 2),
                "signal": ("STRONG_EXPANSION"   if chg >  5 else
                           "EXPANDING"          if chg >  2 else
                           "STRONG_CONTRACTION" if chg < -5 else
                           "CONTRACTING"        if chg < -2 else
                           "STABLE"),
                "available": True,
            }
    except Exception:
        pass

    # Binance fallback
    try:
        pair = "BTCUSDT" if coin == "BTC" else "ETHUSDT"
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/openInterest",
            params={"symbol": pair},
            headers=HEADERS, timeout=T,
        )
        if r.status_code == 200:
            oi = float(r.json().get("openInterest", 0))
            # Get price to convert to USD
            pr = requests.get(
                "https://fapi.binance.com/fapi/v1/ticker/price",
                params={"symbol": pair},
                headers=HEADERS, timeout=T,
            )
            price = float(pr.json().get("price", 0)) if pr.status_code == 200 else 1
            oi_usd = oi * price
            return {
                "value_usd":  round(oi_usd / 1e9, 2),
                "change_24h": 0,
                "signal":     "STABLE",
                "available":  True,
            }
    except Exception:
        pass

    return {"value_usd": 0, "change_24h": 0, "signal": "STABLE", "available": False}


# ══════════════════════════════════════════════
#  8. LIQUIDATIONS
#  Coinglass or Binance fallback
# ══════════════════════════════════════════════

def get_liquidations(symbol: str = "BTCUSD") -> dict:
    coin = "BTC" if "BTC" in symbol else "ETH"

    headers_cg = {"coinglassSecret": COINGLASS_KEY} if COINGLASS_KEY else {}

    try:
        data = _get(
            "https://open-api.coinglass.com/public/v2/liquidation_ex",
            params={"symbol": coin, "interval": "1h"},
            headers=headers_cg,
        )
        d = data.get("data", {})
        if d:
            longs  = float(d.get("longLiquidationUsd", 0))
            shorts = float(d.get("shortLiquidationUsd", 0))
            return {
                "longs_1h":  round(longs / 1e6, 2),
                "shorts_1h": round(shorts / 1e6, 2),
                "signal": ("BEARISH" if longs > shorts * 1.5
                           else "BULLISH" if shorts > longs * 1.5
                           else "NEUTRAL"),
                "available": True,
            }
    except Exception:
        pass

    # Binance liquidation orders fallback
    try:
        pair = "BTCUSDT" if coin == "BTC" else "ETHUSDT"
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/allForceOrders",
            params={"symbol": pair, "limit": 100},
            headers=HEADERS, timeout=T,
        )
        if r.status_code == 200:
            orders = r.json()
            longs  = sum(float(o.get("origQty",0)) * float(o.get("price",0))
                        for o in orders if o.get("side") == "SELL")
            shorts = sum(float(o.get("origQty",0)) * float(o.get("price",0))
                        for o in orders if o.get("side") == "BUY")
            return {
                "longs_1h":  round(longs / 1e6, 2),
                "shorts_1h": round(shorts / 1e6, 2),
                "signal": ("BEARISH" if longs > shorts * 1.5
                           else "BULLISH" if shorts > longs * 1.5
                           else "NEUTRAL"),
                "available": True,
            }
    except Exception:
        pass

    return {"longs_1h": 0, "shorts_1h": 0, "signal": "NEUTRAL", "available": False}


# ══════════════════════════════════════════════
#  9. LONG/SHORT RATIO
#  Binance (free, no key)
# ══════════════════════════════════════════════

def get_long_short_ratio(symbol: str = "BTCUSD") -> dict:
    coin = "BTC" if "BTC" in symbol else "ETH"
    pair = "BTCUSDT" if coin == "BTC" else "ETHUSDT"
    try:
        r = requests.get(
            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
            params={"symbol": pair, "period": "1h", "limit": 1},
            headers=HEADERS, timeout=T,
        )
        if r.status_code == 200 and r.json():
            d     = r.json()[-1]
            ratio = float(d.get("longShortRatio", 1))
            longs = round(ratio / (1 + ratio) * 100, 1)
            return {
                "ratio":  round(ratio, 3),
                "longs":  longs,
                "shorts": round(100 - longs, 1),
                "signal": ("CROWDED_LONGS"  if longs > 65
                           else "CROWDED_SHORTS" if longs < 35
                           else "BALANCED"),
                "available": True,
            }
    except Exception:
        pass
    return {"ratio": 1.0, "longs": 50.0, "shorts": 50.0,
            "signal": "BALANCED", "available": False}


# ══════════════════════════════════════════════
#  10. ECONOMIC EVENTS
# ══════════════════════════════════════════════

def get_economic_events() -> list:
    HIGH = ["CPI","FOMC","NFP","GDP","PPI","INTEREST RATE","FED","POWELL",
            "INFLATION","UNEMPLOYMENT","PAYROLL","TARIFF","TRUMP",
            "CRYPTO","REGULATION","SEC","ETF","BITCOIN","ETHEREUM","RATE DECISION"]
    events = []

    for feed, source in [
        ("https://tradingeconomics.com/rss/news.aspx", "TradingEconomics"),
        ("https://feeds.reuters.com/reuters/businessNews", "Reuters"),
        ("https://feeds.bloomberg.com/markets/news.rss", "Bloomberg"),
    ]:
        for item in _rss(feed, 25):
            title = item.get("title", "")
            if any(k in title.upper() for k in HIGH):
                events.append({
                    "title":     title,
                    "url":       item.get("link", ""),
                    "source":    source,
                    "impact":    "HIGH",
                    "published": item.get("pubDate", "")[:16],
                })

    seen, out = set(), []
    for e in events:
        k = e["title"][:40]
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out[:10]


# ══════════════════════════════════════════════
#  MARKET REGIME SCORE
# ══════════════════════════════════════════════

def calc_regime_score(fg, markets, funding, oi, liq, ls,
                      news, whales, events, coin_data) -> dict:
    score    = 50
    drivers  = []
    risks    = []

    # Fear & Greed
    fgv = fg.get("value", 50)
    if fg.get("available"):
        fgs = fg.get("signal", "NEUTRAL")
        if fgs == "EXTREME_FEAR":
            score += 12; drivers.append(f"Extreme Fear ({fgv}) — contrarian buy signal")
        elif fgs == "FEAR":
            score += 6;  drivers.append(f"Fear ({fgv}) — mild bullish contrarian")
        elif fgs == "EXTREME_GREED":
            score -= 12; risks.append(f"Extreme Greed ({fgv}) — high reversal risk")
        elif fgs == "GREED":
            score -= 6;  risks.append(f"Greed ({fgv}) — elevated caution")

    # Market cap expansion
    if markets.get("available"):
        chg = markets.get("mcap_change_24h", 0)
        if chg > 5:
            score += 10; drivers.append(f"Market cap expanding strongly (+{chg:.1f}%)")
        elif chg > 2:
            score += 5;  drivers.append(f"Market growing (+{chg:.1f}%)")
        elif chg < -5:
            score -= 10; risks.append(f"Market cap collapsing ({chg:.1f}%)")
        elif chg < -2:
            score -= 5;  risks.append(f"Market declining ({chg:.1f}%)")

    # DXY
    if markets.get("dxy_available"):
        dxy_chg = markets.get("dxy_change", 0)
        if dxy_chg < -0.3:
            score += 8;  drivers.append(f"DXY weakening ({dxy_chg:+.2f}) — risk-on")
        elif dxy_chg < -0.1:
            score += 4;  drivers.append(f"Dollar slightly weak ({dxy_chg:+.2f})")
        elif dxy_chg > 0.3:
            score -= 8;  risks.append(f"DXY strengthening ({dxy_chg:+.2f}) — risk-off")
        elif dxy_chg > 0.1:
            score -= 4;  risks.append(f"Dollar gaining strength ({dxy_chg:+.2f})")

    # Coin 24h performance
    if coin_data.get("available"):
        chg24 = coin_data.get("change_24h", 0)
        if chg24 > 3:
            score += 6;  drivers.append(f"Strong 24h momentum (+{chg24:.1f}%)")
        elif chg24 > 1:
            score += 3;  drivers.append(f"Positive momentum (+{chg24:.1f}%)")
        elif chg24 < -3:
            score -= 6;  risks.append(f"Selling pressure (-{abs(chg24):.1f}% 24h)")
        elif chg24 < -1:
            score -= 3;  risks.append(f"Mild decline ({chg24:.1f}% 24h)")

    # Funding rates
    if funding.get("available"):
        fs = funding.get("signal", "NEUTRAL")
        if fs == "OVERHEATED_LONGS":
            score -= 10; risks.append("Funding overheated — long liquidation risk")
        elif fs == "LONGS_DOMINANT":
            score -= 4;  risks.append("Longs dominant in funding")
        elif fs == "OVERHEATED_SHORTS":
            score += 10; drivers.append("Shorts heavily overloaded — squeeze potential")
        elif fs == "SHORTS_DOMINANT":
            score += 4;  drivers.append("Shorts dominant — squeeze risk")

    # Open interest
    if oi.get("available"):
        ois = oi.get("signal", "STABLE")
        if ois == "STRONG_EXPANSION":
            score += 8;  drivers.append(f"OI expanding strongly (+{oi.get('change_24h',0):.1f}%)")
        elif ois == "EXPANDING":
            score += 4;  drivers.append("Open interest growing — new money entering")
        elif ois == "STRONG_CONTRACTION":
            score -= 8;  risks.append("OI collapsing — capital exiting market")
        elif ois == "CONTRACTING":
            score -= 4;  risks.append("Open interest shrinking")

    # Liquidations
    if liq.get("available"):
        ls2 = liq.get("signal", "NEUTRAL")
        if ls2 == "BULLISH":
            score += 6;  drivers.append(f"Short liquidations dominant (${liq.get('shorts_1h',0):.1f}M)")
        elif ls2 == "BEARISH":
            score -= 6;  risks.append(f"Long liquidations dominant (${liq.get('longs_1h',0):.1f}M)")

    # Long/short ratio
    if ls.get("available"):
        longs_pct = ls.get("longs", 50)
        if longs_pct > 65:
            score -= 5;  risks.append(f"Retail over-long ({longs_pct:.0f}% longs)")
        elif longs_pct < 35:
            score += 5;  drivers.append(f"Retail over-short ({longs_pct:.0f}% longs) — squeeze risk")

    # News sentiment
    bull_n = sum(1 for n in news if n.get("sentiment") == "BULLISH")
    bear_n = sum(1 for n in news if n.get("sentiment") == "BEARISH")
    imp_n  = sum(1 for n in news if n.get("important"))
    if bull_n > bear_n + 2:
        pts = min(8, bull_n * 2)
        score += pts; drivers.append(f"News bullish ({bull_n} positive vs {bear_n} negative)")
    elif bear_n > bull_n + 2:
        pts = min(8, bear_n * 2)
        score -= pts; risks.append(f"News bearish ({bear_n} negative vs {bull_n} positive)")
    if imp_n > 0:
        risks.append(f"{imp_n} high-impact news events — volatility likely")

    # Whale transactions
    bull_w = sum(1 for w in whales if w.get("signal") == "BULLISH")
    bear_w = sum(1 for w in whales if w.get("signal") == "BEARISH")
    if bull_w > bear_w:
        score += min(6, bull_w * 2)
        drivers.append(f"{bull_w} whale withdrawals from exchanges — accumulation")
    elif bear_w > bull_w:
        score -= min(6, bear_w * 2)
        risks.append(f"{bear_w} whale deposits to exchanges — distribution")

    # Economic events
    if events:
        risks.append(f"{len(events)} high-impact macro events — expect volatility")

    score = max(0, min(100, score))

    if score >= 72:   regime = "STRONGLY BULLISH"
    elif score >= 57: regime = "BULLISH"
    elif score >= 44: regime = "NEUTRAL"
    elif score >= 29: regime = "BEARISH"
    else:             regime = "STRONGLY BEARISH"

    total_pts  = sum(abs(score - 50) for _ in [1])
    confidence = min(95, 50 + abs(score - 50))

    primary = drivers[0] if drivers else "No strong directional signal"
    risk    = risks[0]   if risks   else "No major risks identified"

    return {
        "score":          score,
        "regime":         regime,
        "confidence":     int(confidence),
        "primary_driver": primary,
        "risk":           risk,
        "invalidation":   ("Loses 4H market structure" if score >= 57
                          else "Breaks key resistance" if score <= 43
                          else "No clear invalidation"),
        "bull_factors":   drivers[:5],
        "bear_factors":   risks[:5],
    }


# ══════════════════════════════════════════════
#  FULL INTELLIGENCE SNAPSHOT
# ══════════════════════════════════════════════

def get_intelligence(symbol: str = "BTCUSD") -> dict:
    fg         = get_fear_greed()
    markets    = get_global_markets()
    coin_data  = get_coin_data(symbol)
    news       = get_crypto_news(symbol, limit=30)
    funding    = get_funding_rates(symbol)
    oi         = get_open_interest(symbol)
    liq        = get_liquidations(symbol)
    ls         = get_long_short_ratio(symbol)
    whales     = get_whale_transactions(symbol)
    events     = get_economic_events()

    regime = calc_regime_score(
        fg, markets, funding, oi, liq, ls,
        news, whales, events, coin_data
    )

    return {
        "symbol":          symbol,
        "fear_greed":      fg,
        "global_markets":  markets,
        "coin_data":       coin_data,
        "news":            news,
        "funding":         funding,
        "open_interest":   oi,
        "liquidations":    liq,
        "long_short":      ls,
        "whale_txns":      whales,
        "economic_events": events,
        "regime":          regime,
        "high_impact_event": len(events) > 0,
        "timestamp":       datetime.now(timezone.utc).strftime("%H:%M UTC"),
    }


def intelligence_score(intel: dict, side: str) -> int:
    """Bonus confidence points from regime score."""
    if not intel:
        return 0
    rs    = intel.get("regime", {}).get("score", 50)
    is_buy = side == "BUY"
    if is_buy  and rs >= 65: return min(15, int((rs - 50) / 3))
    if not is_buy and rs <= 35: return min(15, int((50 - rs) / 3))
    return 0
