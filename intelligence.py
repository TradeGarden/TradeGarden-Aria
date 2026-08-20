"""
intelligence.py - Aria Complete Intelligence Engine
====================================================
All free public APIs, no keys required.

Sources:
  1. Fear & Greed       - alternative.me
  2. Crypto News        - cryptopanic.com + rss2json
  3. Funding Rates      - coinglass.com
  4. Open Interest      - coinglass.com
  5. Liquidations       - coinglass.com
  6. Long/Short Ratio   - coinglass.com
  7. Whale Transactions - whale-alert via rss2json
  8. Exchange Flows     - coinglass.com
  9. Economic Calendar  - tradingeconomics rss
 10. Global Market Data - DXY, Gold, Nasdaq via stooq
 11. BTC Dominance      - coingecko
 12. Crypto Total MCap  - coingecko
"""

import requests
from datetime import datetime, timezone

T = 12  # request timeout seconds


def _get(url, params=None, headers=None) -> dict:
    try:
        r = requests.get(url, params=params, headers=headers, timeout=T)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def _rss(url: str, count: int = 20) -> list:
    try:
        r = requests.get(
            "https://api.rss2json.com/v1/api.json",
            params={"rss_url": url, "count": count},
            timeout=T,
        )
        return r.json().get("items", []) if r.status_code == 200 else []
    except Exception:
        return []


# ══════════════════════════════════════════════
#  1. FEAR & GREED
# ══════════════════════════════════════════════

def get_fear_greed() -> dict:
    try:
        data = _get("https://api.alternative.me/fng/?limit=3")
        items = data.get("data", [])
        if items:
            cur = items[0]
            val = int(cur["value"])
            prev = int(items[1]["value"]) if len(items) > 1 else val
            return {
                "value":     val,
                "label":     cur["value_classification"],
                "previous":  prev,
                "change":    val - prev,
                "signal":    ("EXTREME_FEAR" if val <= 25 else
                              "FEAR"         if val <= 45 else
                              "NEUTRAL"      if val <= 55 else
                              "GREED"        if val <= 75 else
                              "EXTREME_GREED"),
            }
    except Exception:
        pass
    return {"value": 50, "label": "Neutral", "previous": 50,
            "change": 0, "signal": "NEUTRAL"}


# ══════════════════════════════════════════════
#  2. CRYPTO NEWS (BTC + ETH)
# ══════════════════════════════════════════════

def get_crypto_news(limit: int = 30) -> list:
    news = []

    # Source 1: CryptoPanic free
    for coin in ["BTC", "ETH"]:
        try:
            r = requests.get(
                "https://cryptopanic.com/api/free/v1/posts/",
                params={"auth_token": "free", "currencies": coin,
                        "kind": "news", "public": "true"},
                timeout=T,
            )
            if r.status_code == 200:
                for p in r.json().get("results", [])[:10]:
                    votes = p.get("votes", {})
                    bull  = votes.get("positive", 0)
                    bear  = votes.get("negative", 0)
                    sent  = ("BULLISH" if bull > bear * 1.3
                             else "BEARISH" if bear > bull * 1.3
                             else "NEUTRAL")
                    news.append({
                        "title":     p.get("title", ""),
                        "url":       p.get("url", ""),
                        "source":    p.get("source", {}).get("title", ""),
                        "coin":      coin,
                        "sentiment": sent,
                        "important": bool(p.get("is_important")),
                        "published": p.get("published_at", "")[:16],
                        "votes_bull": bull,
                        "votes_bear": bear,
                    })
        except Exception:
            pass

    # Source 2: CoinDesk RSS fallback
    if len(news) < 5:
        for item in _rss("https://www.coindesk.com/arc/outboundfeeds/rss/", 20):
            title = item.get("title", "")
            coin  = "BTC" if "bitcoin" in title.lower() or "btc" in title.lower() else \
                    "ETH" if "ethereum" in title.lower() or "eth" in title.lower() else "CRYPTO"
            news.append({
                "title":     title,
                "url":       item.get("link", ""),
                "source":    "CoinDesk",
                "coin":      coin,
                "sentiment": "NEUTRAL",
                "important": False,
                "published": item.get("pubDate", "")[:16],
                "votes_bull": 0,
                "votes_bear": 0,
            })

    # Source 3: Decrypt RSS
    if len(news) < 10:
        for item in _rss("https://decrypt.co/feed", 15):
            title = item.get("title", "")
            news.append({
                "title":     title,
                "url":       item.get("link", ""),
                "source":    "Decrypt",
                "coin":      "CRYPTO",
                "sentiment": "NEUTRAL",
                "important": False,
                "published": item.get("pubDate", "")[:16],
                "votes_bull": 0,
                "votes_bear": 0,
            })

    # Deduplicate
    seen, unique = set(), []
    for n in news:
        key = n["title"][:50]
        if key not in seen:
            seen.add(key)
            unique.append(n)

    return unique[:limit]


# ══════════════════════════════════════════════
#  3. FUNDING RATES
# ══════════════════════════════════════════════

def get_funding_rates() -> dict:
    result = {}
    for coin in ["BTC", "ETH"]:
        try:
            data = _get("https://open-api.coinglass.com/public/v2/funding",
                        params={"symbol": coin})
            items = data.get("data", [])
            rates = {}
            total = 0.0
            count = 0
            for ex in items:
                rate = float(ex.get("rate", 0))
                rates[ex.get("exchangeName", "?")] = round(rate * 100, 4)
                total += rate
                count += 1
            avg = total / count if count else 0
            result[coin] = {
                "average":    round(avg * 100, 4),
                "annualized": round(avg * 3 * 365 * 100, 1),
                "exchanges":  rates,
                "signal": ("OVERHEATED_LONGS"  if avg >  0.0005 else
                           "LONGS_DOMINANT"    if avg >  0.0001 else
                           "OVERHEATED_SHORTS" if avg < -0.0005 else
                           "SHORTS_DOMINANT"   if avg < -0.0001 else
                           "NEUTRAL"),
            }
        except Exception:
            result[coin] = {"average": 0, "annualized": 0,
                            "exchanges": {}, "signal": "NEUTRAL"}
    return result


# ══════════════════════════════════════════════
#  4. OPEN INTEREST
# ══════════════════════════════════════════════

def get_open_interest() -> dict:
    result = {}
    for coin in ["BTC", "ETH"]:
        try:
            data = _get("https://open-api.coinglass.com/public/v2/open_interest",
                        params={"symbol": coin})
            d = data.get("data", {})
            oi  = float(d.get("openInterest", 0))
            chg = float(d.get("openInterestChange24h", 0))
            result[coin] = {
                "value_usd":    round(oi / 1e9, 2),
                "change_24h":   round(chg, 2),
                "signal": ("STRONG_EXPANSION"   if chg >  5 else
                           "EXPANDING"          if chg >  2 else
                           "STRONG_CONTRACTION" if chg < -5 else
                           "CONTRACTING"        if chg < -2 else
                           "STABLE"),
            }
        except Exception:
            result[coin] = {"value_usd": 0, "change_24h": 0, "signal": "STABLE"}
    return result


# ══════════════════════════════════════════════
#  5. LIQUIDATIONS
# ══════════════════════════════════════════════

def get_liquidations() -> dict:
    result = {}
    for coin in ["BTC", "ETH"]:
        try:
            data = _get("https://open-api.coinglass.com/public/v2/liquidation_ex",
                        params={"symbol": coin, "interval": "1h"})
            d = data.get("data", {})
            longs  = float(d.get("longLiquidationUsd", 0))
            shorts = float(d.get("shortLiquidationUsd", 0))
            result[coin] = {
                "longs_1h":  round(longs  / 1e6, 2),
                "shorts_1h": round(shorts / 1e6, 2),
                "dominant":  "LONGS_LIQUIDATED" if longs > shorts * 1.5
                             else "SHORTS_LIQUIDATED" if shorts > longs * 1.5
                             else "BALANCED",
                "signal": ("BEARISH" if longs > shorts * 1.5
                           else "BULLISH" if shorts > longs * 1.5
                           else "NEUTRAL"),
            }
        except Exception:
            result[coin] = {"longs_1h": 0, "shorts_1h": 0,
                            "dominant": "BALANCED", "signal": "NEUTRAL"}
    return result


# ══════════════════════════════════════════════
#  6. LONG / SHORT RATIO
# ══════════════════════════════════════════════

def get_long_short_ratio() -> dict:
    result = {}
    for coin in ["BTC", "ETH"]:
        try:
            data = _get("https://open-api.coinglass.com/public/v2/global_long_short_account_ratio",
                        params={"symbol": coin, "interval": "1h", "limit": 1})
            items = data.get("data", [])
            if items:
                ls = items[-1]
                ratio = float(ls.get("longShortRatio", 1))
                longs = float(ls.get("longAccount", 50))
                result[coin] = {
                    "ratio":  round(ratio, 3),
                    "longs":  round(longs, 1),
                    "shorts": round(100 - longs, 1),
                    "signal": ("CROWDED_LONGS"  if longs > 65 else
                               "CROWDED_SHORTS" if longs < 35 else
                               "BALANCED"),
                }
                continue
        except Exception:
            pass
        result[coin] = {"ratio": 1.0, "longs": 50, "shorts": 50, "signal": "BALANCED"}
    return result


# ══════════════════════════════════════════════
#  7. WHALE TRANSACTIONS
# ══════════════════════════════════════════════

def get_whale_transactions() -> list:
    whales = []

    # Whale Alert RSS (free public feed)
    items = _rss("https://whale-alert.io/rss/all", 20)
    for item in items:
        title = item.get("title", "")
        if not title:
            continue
        # Parse: "999 #BTC transferred from unknown to Coinbase"
        coin = "BTC" if "BTC" in title.upper() or "BITCOIN" in title.upper() \
               else "ETH" if "ETH" in title.upper() or "ETHEREUM" in title.upper() \
               else None
        if not coin:
            continue

        # Determine direction
        title_l = title.lower()
        direction = ("TO_EXCHANGE"   if "to coinbase" in title_l or
                                        "to binance" in title_l or
                                        "to kraken" in title_l or
                                        "to exchange" in title_l
                     else "FROM_EXCHANGE" if "from coinbase" in title_l or
                                            "from binance" in title_l or
                                            "from exchange" in title_l
                     else "WALLET_TO_WALLET")

        signal = ("BEARISH" if direction == "TO_EXCHANGE"
                  else "BULLISH" if direction == "FROM_EXCHANGE"
                  else "NEUTRAL")

        whales.append({
            "title":     title,
            "url":       item.get("link", ""),
            "coin":      coin,
            "direction": direction,
            "signal":    signal,
            "published": item.get("pubDate", "")[:16],
        })

    return whales[:15]


# ══════════════════════════════════════════════
#  8. EXCHANGE FLOWS (BTC)
# ══════════════════════════════════════════════

def get_exchange_flows() -> dict:
    result = {}
    for coin in ["BTC", "ETH"]:
        try:
            data = _get("https://open-api.coinglass.com/public/v2/exchange_flows",
                        params={"symbol": coin})
            d = data.get("data", {})
            inflow  = float(d.get("inflow24h",  0))
            outflow = float(d.get("outflow24h", 0))
            net     = outflow - inflow
            result[coin] = {
                "inflow_24h":  round(inflow  / 1e6, 2),
                "outflow_24h": round(outflow / 1e6, 2),
                "net_flow":    round(net     / 1e6, 2),
                "signal": ("BULLISH" if net >  50 else
                           "BEARISH" if net < -50 else
                           "NEUTRAL"),
                "interpretation": (
                    "More BTC leaving exchanges — holders accumulating" if net > 50
                    else "More BTC entering exchanges — potential selling pressure" if net < -50
                    else "Balanced flow"
                ),
            }
        except Exception:
            result[coin] = {"inflow_24h": 0, "outflow_24h": 0,
                            "net_flow": 0, "signal": "NEUTRAL",
                            "interpretation": "Data unavailable"}
    return result


# ══════════════════════════════════════════════
#  9. ECONOMIC CALENDAR
# ══════════════════════════════════════════════

def get_economic_events() -> list:
    HIGH_IMPACT = ["CPI","FOMC","NFP","GDP","PPI","INTEREST RATE",
                   "FED","POWELL","INFLATION","UNEMPLOYMENT","PAYROLL",
                   "RATE DECISION","JEROME","YELLEN","TREASURY",
                   "TARIFF","TRUMP","CRYPTO REGULATION","SEC","ETF"]
    events = []

    # TradingEconomics RSS
    for item in _rss("https://tradingeconomics.com/rss/news.aspx", 30):
        title = item.get("title", "")
        if any(k in title.upper() for k in HIGH_IMPACT):
            events.append({
                "title":     title,
                "url":       item.get("link", ""),
                "source":    "TradingEconomics",
                "impact":    "HIGH",
                "published": item.get("pubDate", "")[:16],
            })

    # Reuters RSS for macro
    for item in _rss("https://feeds.reuters.com/reuters/businessNews", 20):
        title = item.get("title", "")
        if any(k in title.upper() for k in HIGH_IMPACT):
            events.append({
                "title":     title,
                "url":       item.get("link", ""),
                "source":    "Reuters",
                "impact":    "HIGH",
                "published": item.get("pubDate", "")[:16],
            })

    # Deduplicate
    seen, unique = set(), []
    for e in events:
        key = e["title"][:40]
        if key not in seen:
            seen.add(key)
            unique.append(e)

    return unique[:10]


# ══════════════════════════════════════════════
#  10. GLOBAL MARKET DATA
# ══════════════════════════════════════════════

def get_global_markets() -> dict:
    markets = {}

    # CoinGecko for BTC dominance + total market cap
    try:
        data = _get("https://api.coingecko.com/api/v3/global")
        d = data.get("data", {})
        markets["btc_dominance"]   = round(d.get("market_cap_percentage", {}).get("btc", 0), 1)
        markets["eth_dominance"]   = round(d.get("market_cap_percentage", {}).get("eth", 0), 1)
        markets["total_mcap_usd"]  = round(d.get("total_market_cap",{}).get("usd", 0) / 1e12, 2)
        markets["mcap_change_24h"] = round(d.get("market_cap_change_percentage_24h_usd", 0), 2)
        markets["active_cryptos"]  = d.get("active_cryptocurrencies", 0)
        markets["btc_dominance_signal"] = ("HIGH_DOMINANCE" if markets["btc_dominance"] > 55
                                           else "LOW_DOMINANCE" if markets["btc_dominance"] < 40
                                           else "NORMAL")
    except Exception:
        markets.update({"btc_dominance": 0, "eth_dominance": 0,
                        "total_mcap_usd": 0, "mcap_change_24h": 0,
                        "active_cryptos": 0, "btc_dominance_signal": "NORMAL"})

    # Stooq for DXY (dollar index)
    try:
        r = requests.get("https://stooq.com/q/l/?s=dxy&f=sd2t2ohlcv&h&e=csv",
                         timeout=T)
        lines = r.text.strip().split("\n")
        if len(lines) > 1:
            parts = lines[-1].split(",")
            if len(parts) >= 5:
                markets["dxy_price"]  = float(parts[4])
                markets["dxy_open"]   = float(parts[2])
                markets["dxy_change"] = round(float(parts[4]) - float(parts[2]), 3)
                markets["dxy_signal"] = ("BEARISH_DXY" if markets["dxy_change"] < -0.2
                                         else "BULLISH_DXY" if markets["dxy_change"] > 0.2
                                         else "NEUTRAL")
    except Exception:
        markets.update({"dxy_price": 0, "dxy_open": 0,
                        "dxy_change": 0, "dxy_signal": "NEUTRAL"})

    return markets


# ══════════════════════════════════════════════
#  INTELLIGENCE SCORE (adds to confidence)
# ══════════════════════════════════════════════

def intelligence_score(intel: dict, side: str) -> int:
    if not intel:
        return 0
    score  = 0
    is_buy = side == "BUY"

    fg = intel.get("fear_greed", {}).get("signal", "NEUTRAL")
    if is_buy  and fg == "EXTREME_FEAR":   score += 6
    elif is_buy  and fg == "FEAR":         score += 3
    elif not is_buy and fg == "EXTREME_GREED": score += 6
    elif not is_buy and fg == "GREED":         score += 3

    fund = intel.get("funding", {}).get("BTC", {}).get("signal", "NEUTRAL")
    if is_buy  and fund == "OVERHEATED_SHORTS": score += 5
    elif is_buy  and fund == "SHORTS_DOMINANT": score += 2
    elif not is_buy and fund == "OVERHEATED_LONGS": score += 5
    elif not is_buy and fund == "LONGS_DOMINANT":   score += 2

    news = intel.get("news", [])
    bull = sum(1 for n in news if n.get("sentiment") == "BULLISH")
    bear = sum(1 for n in news if n.get("sentiment") == "BEARISH")
    if is_buy  and bull > bear: score += min(4, bull)
    if not is_buy and bear > bull: score += min(4, bear)

    liq = intel.get("liquidations", {}).get("BTC", {}).get("signal", "NEUTRAL")
    if is_buy  and liq == "BULLISH": score += 5
    if not is_buy and liq == "BEARISH": score += 5

    return min(score, 20)


# ══════════════════════════════════════════════
#  FULL SNAPSHOT
# ══════════════════════════════════════════════

def get_intelligence(symbol: str = "BTCUSD") -> dict:
    """Full intelligence snapshot. All sources. Fails gracefully."""
    fg      = get_fear_greed()
    news    = get_crypto_news(limit=30)
    funding = get_funding_rates()
    oi      = get_open_interest()
    liq     = get_liquidations()
    ls      = get_long_short_ratio()
    whales  = get_whale_transactions()
    flows   = get_exchange_flows()
    events  = get_economic_events()
    markets = get_global_markets()

    coin = "BTC" if "BTC" in symbol else "ETH"

    # Overall signal
    signals = []
    if fg["signal"] in ("EXTREME_FEAR",):          signals.append("BULLISH")
    if fg["signal"] in ("EXTREME_GREED",):         signals.append("BEARISH")
    if funding.get(coin, {}).get("signal") == "OVERHEATED_SHORTS": signals.append("BULLISH")
    if funding.get(coin, {}).get("signal") == "OVERHEATED_LONGS":  signals.append("BEARISH")
    if liq.get(coin, {}).get("signal") == "BULLISH": signals.append("BULLISH")
    if liq.get(coin, {}).get("signal") == "BEARISH": signals.append("BEARISH")
    bull_n = sum(1 for n in news if n.get("sentiment") == "BULLISH")
    bear_n = sum(1 for n in news if n.get("sentiment") == "BEARISH")
    if bull_n > bear_n: signals.append("BULLISH")
    if bear_n > bull_n: signals.append("BEARISH")

    bull_count = signals.count("BULLISH")
    bear_count = signals.count("BEARISH")
    overall = ("BULLISH" if bull_count > bear_count
               else "BEARISH" if bear_count > bull_count
               else "NEUTRAL")

    return {
        "symbol":         symbol,
        "fear_greed":     fg,
        "news":           news,
        "funding":        funding,
        "open_interest":  oi,
        "liquidations":   liq,
        "long_short":     ls,
        "whale_txns":     whales,
        "exchange_flows": flows,
        "economic_events":events,
        "global_markets": markets,
        "overall_signal": overall,
        "high_impact_event": len(events) > 0,
        "timestamp":      datetime.now(timezone.utc).strftime("%H:%M UTC"),
    }
