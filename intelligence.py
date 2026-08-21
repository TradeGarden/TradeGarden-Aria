"""
intelligence.py - Aria Intelligence Engine
==========================================
Market regime scoring for BTC and ETH.
Feeds into trading decisions AND intelligence page.

INTELLIGENCE SCORE: 0-100
Bullish: market expansion, fear, momentum, structure, flows
Bearish: DXY strength, liquidations, funding overload, news

All free APIs - no keys needed.
"""

import requests
from datetime import datetime, timezone

T = 10  # timeout seconds


def _get(url, params=None):
    try:
        r = requests.get(url, params=params, timeout=T)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def _rss(url, count=20):
    try:
        r = requests.get(
            "https://api.rss2json.com/v1/api.json",
            params={"rss_url": url, "count": count},
            timeout=T,
        )
        return r.json().get("items", []) if r.status_code == 200 else []
    except Exception:
        return []


# ── Fear & Greed ──────────────────────────────────────────────────────────

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
            }
    except Exception:
        pass
    return {"value": 50, "label": "N/A", "previous": 50,
            "change": 0, "signal": "NEUTRAL"}


# ── Global markets ────────────────────────────────────────────────────────

def get_global_markets() -> dict:
    out = {
        "btc_dominance": 0, "eth_dominance": 0,
        "total_mcap_usd": 0, "mcap_change_24h": 0,
        "dxy_price": 0, "dxy_change": 0,
        "dxy_available": False,
    }
    try:
        data = _get("https://api.coingecko.com/api/v3/global")
        d    = data.get("data", {})
        out["btc_dominance"]   = round(d.get("market_cap_percentage",{}).get("btc",0), 1)
        out["eth_dominance"]   = round(d.get("market_cap_percentage",{}).get("eth",0), 1)
        out["total_mcap_usd"]  = round(d.get("total_market_cap",{}).get("usd",0)/1e12, 2)
        out["mcap_change_24h"] = round(d.get("market_cap_change_percentage_24h_usd",0), 2)
    except Exception:
        pass

    # DXY
    try:
        r = requests.get("https://stooq.com/q/l/?s=dxy&f=sd2t2ohlcv&h&e=csv", timeout=T)
        lines = r.text.strip().split("\n")
        if len(lines) > 1:
            parts = lines[-1].split(",")
            if len(parts) >= 5:
                price = float(parts[4])
                open_ = float(parts[2])
                if price > 50:  # sanity check - DXY is usually 90-115
                    out["dxy_price"]     = price
                    out["dxy_change"]    = round(price - open_, 3)
                    out["dxy_available"] = True
    except Exception:
        pass

    return out


# ── Crypto news ───────────────────────────────────────────────────────────

def get_crypto_news(symbol: str = "BTCUSD", limit: int = 30) -> list:
    coin = "BTC" if "BTC" in symbol else "ETH"
    news = []

    # CryptoPanic
    try:
        r = requests.get(
            "https://cryptopanic.com/api/free/v1/posts/",
            params={"auth_token": "free", "currencies": coin,
                    "kind": "news", "public": "true"},
            timeout=T,
        )
        if r.status_code == 200:
            for p in r.json().get("results", [])[:15]:
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
                })
    except Exception:
        pass

    # CoinDesk RSS fallback
    if len(news) < 5:
        for item in _rss("https://www.coindesk.com/arc/outboundfeeds/rss/", 15):
            title = item.get("title", "")
            if coin in title.upper() or ("BITCOIN" in title.upper() and coin=="BTC") \
               or ("ETHEREUM" in title.upper() and coin=="ETH"):
                news.append({
                    "title":     title,
                    "url":       item.get("link", ""),
                    "source":    "CoinDesk",
                    "coin":      coin,
                    "sentiment": "NEUTRAL",
                    "important": False,
                    "published": item.get("pubDate", "")[:16],
                })

    # Decrypt RSS
    if len(news) < 8:
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
            })

    # Deduplicate
    seen, unique = set(), []
    for n in news:
        key = n["title"][:40]
        if key and key not in seen:
            seen.add(key)
            unique.append(n)

    return unique[:limit]


# ── Whale transactions ────────────────────────────────────────────────────

def get_whale_transactions(symbol: str = "BTCUSD") -> list:
    coin   = "BTC" if "BTC" in symbol else "ETH"
    whales = []

    for item in _rss("https://whale-alert.io/rss/all", 25):
        title = item.get("title", "")
        if not title:
            continue
        tu = title.upper()
        if coin not in tu and ("BITCOIN" not in tu or coin != "BTC") \
           and ("ETHEREUM" not in tu or coin != "ETH"):
            continue

        tl = title.lower()
        direction = (
            "TO_EXCHANGE"    if any(x in tl for x in ["to coinbase","to binance","to kraken","to huobi","to okx","to exchange"]) else
            "FROM_EXCHANGE"  if any(x in tl for x in ["from coinbase","from binance","from kraken","from exchange"]) else
            "WALLET_TO_WALLET"
        )
        signal = ("BEARISH" if direction=="TO_EXCHANGE"
                  else "BULLISH" if direction=="FROM_EXCHANGE"
                  else "NEUTRAL")

        whales.append({
            "title":     title,
            "url":       item.get("link",""),
            "coin":      coin,
            "direction": direction,
            "signal":    signal,
            "published": item.get("pubDate","")[:16],
        })

    return whales[:15]


# ── Funding rates ─────────────────────────────────────────────────────────

def get_funding_rates(symbol: str = "BTCUSD") -> dict:
    coin = "BTC" if "BTC" in symbol else "ETH"
    try:
        data  = _get("https://open-api.coinglass.com/public/v2/funding",
                     params={"symbol": coin})
        items = data.get("data", [])
        if items:
            rates  = {}
            total  = 0.0
            count  = 0
            for ex in items:
                rate = float(ex.get("rate", 0))
                rates[ex.get("exchangeName","?")] = round(rate*100, 4)
                total += rate
                count += 1
            avg = total/count if count else 0
            return {
                "average":    round(avg*100, 4),
                "annualized": round(avg*3*365*100, 1),
                "exchanges":  rates,
                "signal": ("OVERHEATED_LONGS"  if avg >  0.0005 else
                           "LONGS_DOMINANT"    if avg >  0.0001 else
                           "OVERHEATED_SHORTS" if avg < -0.0005 else
                           "SHORTS_DOMINANT"   if avg < -0.0001 else
                           "NEUTRAL"),
            }
    except Exception:
        pass
    return {"average": 0, "annualized": 0, "exchanges": {}, "signal": "NEUTRAL"}


# ── Open interest ─────────────────────────────────────────────────────────

def get_open_interest(symbol: str = "BTCUSD") -> dict:
    coin = "BTC" if "BTC" in symbol else "ETH"
    try:
        data = _get("https://open-api.coinglass.com/public/v2/open_interest",
                    params={"symbol": coin})
        d    = data.get("data", {})
        oi   = float(d.get("openInterest", 0))
        chg  = float(d.get("openInterestChange24h", 0))
        return {
            "value_usd":  round(oi/1e9, 2),
            "change_24h": round(chg, 2),
            "signal": ("STRONG_EXPANSION"   if chg >  5 else
                       "EXPANDING"          if chg >  2 else
                       "STRONG_CONTRACTION" if chg < -5 else
                       "CONTRACTING"        if chg < -2 else
                       "STABLE"),
        }
    except Exception:
        return {"value_usd": 0, "change_24h": 0, "signal": "STABLE"}


# ── Liquidations ──────────────────────────────────────────────────────────

def get_liquidations(symbol: str = "BTCUSD") -> dict:
    coin = "BTC" if "BTC" in symbol else "ETH"
    try:
        data   = _get("https://open-api.coinglass.com/public/v2/liquidation_ex",
                      params={"symbol": coin, "interval": "1h"})
        d      = data.get("data", {})
        longs  = float(d.get("longLiquidationUsd", 0))
        shorts = float(d.get("shortLiquidationUsd", 0))
        return {
            "longs_1h":  round(longs/1e6, 2),
            "shorts_1h": round(shorts/1e6, 2),
            "signal": ("BEARISH" if longs > shorts*1.5
                       else "BULLISH" if shorts > longs*1.5
                       else "NEUTRAL"),
        }
    except Exception:
        return {"longs_1h": 0, "shorts_1h": 0, "signal": "NEUTRAL"}


# ── Economic events ───────────────────────────────────────────────────────

def get_economic_events() -> list:
    HIGH = ["CPI","FOMC","NFP","GDP","PPI","INTEREST RATE","FED",
            "POWELL","INFLATION","UNEMPLOYMENT","PAYROLL","TARIFF",
            "TRUMP","CRYPTO REGULATION","SEC","ETF","BITCOIN","ETHEREUM"]
    events = []
    for item in _rss("https://tradingeconomics.com/rss/news.aspx", 30):
        title = item.get("title","")
        if any(k in title.upper() for k in HIGH):
            events.append({
                "title":     title,
                "url":       item.get("link",""),
                "source":    "TradingEconomics",
                "impact":    "HIGH",
                "published": item.get("pubDate","")[:16],
            })
    for item in _rss("https://feeds.reuters.com/reuters/businessNews", 15):
        title = item.get("title","")
        if any(k in title.upper() for k in HIGH):
            events.append({
                "title":     title,
                "url":       item.get("link",""),
                "source":    "Reuters",
                "impact":    "HIGH",
                "published": item.get("pubDate","")[:16],
            })
    seen, out = set(), []
    for e in events:
        k = e["title"][:40]
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out[:8]


# ══════════════════════════════════════════════
#  MARKET REGIME SCORE
#  The core intelligence signal: 0-100
# ══════════════════════════════════════════════

def calc_regime_score(
    fg: dict, markets: dict, funding: dict, oi: dict,
    liq: dict, news: list, whales: list, events: list, symbol: str
) -> dict:
    """
    Score the market from 0-100.
    0-35  = BEARISH
    36-55 = NEUTRAL
    56-70 = BULLISH
    71-100 = STRONGLY BULLISH

    Bullish factors add points, bearish factors subtract.
    Base is 50 (neutral).
    """
    score    = 50
    drivers  = []
    risks    = []
    bull_pts = 0
    bear_pts = 0

    # 1. Fear & Greed (max ±15pts)
    fgv = fg.get("value", 50)
    fgs = fg.get("signal","NEUTRAL")
    if fgs == "EXTREME_FEAR":
        score += 12; bull_pts += 12
        drivers.append("Extreme Fear — historically bullish contrarian signal")
    elif fgs == "FEAR":
        score += 6;  bull_pts += 6
        drivers.append("Market fear — mild bullish bias")
    elif fgs == "EXTREME_GREED":
        score -= 12; bear_pts += 12
        risks.append("Extreme Greed — elevated reversal risk")
    elif fgs == "GREED":
        score -= 6;  bear_pts += 6
        risks.append("Elevated greed — watch for pullback")

    # 2. Market cap expansion (max +10pts)
    mcap_chg = markets.get("mcap_change_24h", 0)
    if mcap_chg > 5:
        score += 10; bull_pts += 10
        drivers.append(f"Total market cap expanding strongly (+{mcap_chg:.1f}%)")
    elif mcap_chg > 2:
        score += 5;  bull_pts += 5
        drivers.append(f"Market cap growing (+{mcap_chg:.1f}%)")
    elif mcap_chg < -5:
        score -= 10; bear_pts += 10
        risks.append(f"Market cap contracting ({mcap_chg:.1f}%)")
    elif mcap_chg < -2:
        score -= 5;  bear_pts += 5
        risks.append(f"Market cap declining ({mcap_chg:.1f}%)")

    # 3. DXY (max ±10pts) — strong dollar = bad for crypto
    if markets.get("dxy_available"):
        dxy_chg = markets.get("dxy_change", 0)
        if dxy_chg < -0.3:
            score += 8; bull_pts += 8
            drivers.append(f"DXY weakening ({dxy_chg:+.2f}) — risk-on for crypto")
        elif dxy_chg < -0.1:
            score += 4; bull_pts += 4
        elif dxy_chg > 0.3:
            score -= 8; bear_pts += 8
            risks.append(f"DXY strengthening ({dxy_chg:+.2f}) — risk-off for crypto")
        elif dxy_chg > 0.1:
            score -= 4; bear_pts += 4

    # 4. Funding rates (max ±10pts)
    fund_sig = funding.get("signal","NEUTRAL")
    if fund_sig == "OVERHEATED_LONGS":
        score -= 10; bear_pts += 10
        risks.append("Funding overheated — longs very crowded, risk of flush")
    elif fund_sig == "LONGS_DOMINANT":
        score -= 4;  bear_pts += 4
        risks.append("Longs dominant in funding")
    elif fund_sig == "OVERHEATED_SHORTS":
        score += 10; bull_pts += 10
        drivers.append("Shorts overheated — short squeeze potential")
    elif fund_sig == "SHORTS_DOMINANT":
        score += 4;  bull_pts += 4
        drivers.append("Shorts dominant — potential squeeze")

    # 5. Open interest (max ±8pts)
    oi_sig = oi.get("signal","STABLE")
    if oi_sig == "STRONG_EXPANSION":
        score += 8; bull_pts += 8
        drivers.append(f"Open interest expanding strongly (+{oi.get('change_24h',0):.1f}%)")
    elif oi_sig == "EXPANDING":
        score += 4; bull_pts += 4
        drivers.append("Open interest growing — new money entering")
    elif oi_sig == "STRONG_CONTRACTION":
        score -= 8; bear_pts += 8
        risks.append("Open interest collapsing — money leaving market")
    elif oi_sig == "CONTRACTING":
        score -= 4; bear_pts += 4

    # 6. Liquidations (max ±8pts)
    liq_sig = liq.get("signal","NEUTRAL")
    if liq_sig == "BULLISH":
        score += 6; bull_pts += 6
        drivers.append(f"Short liquidations dominant (${liq.get('shorts_1h',0):.1f}M)")
    elif liq_sig == "BEARISH":
        score -= 6; bear_pts += 6
        risks.append(f"Long liquidations dominant (${liq.get('longs_1h',0):.1f}M)")

    # 7. News sentiment (max ±8pts)
    bull_news = sum(1 for n in news if n.get("sentiment")=="BULLISH")
    bear_news = sum(1 for n in news if n.get("sentiment")=="BEARISH")
    imp_news  = sum(1 for n in news if n.get("important"))
    if bull_news > bear_news + 2:
        pts = min(8, bull_news * 2)
        score += pts; bull_pts += pts
        drivers.append(f"News sentiment bullish ({bull_news} bullish vs {bear_news} bearish)")
    elif bear_news > bull_news + 2:
        pts = min(8, bear_news * 2)
        score -= pts; bear_pts += pts
        risks.append(f"News sentiment bearish ({bear_news} bearish vs {bull_news} bullish)")
    if imp_news > 0:
        risks.append(f"{imp_news} high-impact news event(s) — increased volatility")

    # 8. Whale transactions (max ±6pts)
    bull_whales = sum(1 for w in whales if w.get("signal")=="BULLISH")
    bear_whales = sum(1 for w in whales if w.get("signal")=="BEARISH")
    if bull_whales > bear_whales:
        score += min(6, bull_whales * 2); bull_pts += min(6, bull_whales * 2)
        drivers.append(f"{bull_whales} whale withdrawals from exchanges — accumulation")
    elif bear_whales > bull_whales:
        score -= min(6, bear_whales * 2); bear_pts += min(6, bear_whales * 2)
        risks.append(f"{bear_whales} whale deposits to exchanges — potential sell pressure")

    # 9. High impact events (warning)
    if events:
        risks.append(f"{len(events)} high-impact economic event(s) today — expect volatility")

    # Clamp score
    score = max(0, min(100, score))

    # Determine regime
    if score >= 71:   regime = "STRONGLY BULLISH"
    elif score >= 56: regime = "BULLISH"
    elif score >= 45: regime = "NEUTRAL"
    elif score >= 30: regime = "BEARISH"
    else:             regime = "STRONGLY BEARISH"

    # Primary driver
    primary = drivers[0] if drivers else "No strong directional signal"

    # Invalidation condition
    coin  = "BTC" if "BTC" in symbol else "ETH"
    inval = (f"{coin} loses key 4H market structure" if score >= 56
             else f"{coin} breaks above key resistance" if score <= 45
             else "No clear invalidation identified")

    # Confidence
    total_pts  = bull_pts + bear_pts
    confidence = round(min(95, 50 + (abs(bull_pts - bear_pts) / max(total_pts,1)) * 50), 0)

    return {
        "score":           score,
        "regime":          regime,
        "confidence":      int(confidence),
        "primary_driver":  primary,
        "risk":            risks[0] if risks else "No major risks identified",
        "invalidation":    inval,
        "bull_factors":    drivers,
        "bear_factors":    risks,
        "bull_pts":        bull_pts,
        "bear_pts":        bear_pts,
    }


# ══════════════════════════════════════════════
#  FULL INTELLIGENCE SNAPSHOT
# ══════════════════════════════════════════════

def get_intelligence(symbol: str = "BTCUSD") -> dict:
    """Complete intelligence snapshot for one symbol."""
    fg      = get_fear_greed()
    markets = get_global_markets()
    news    = get_crypto_news(symbol, limit=30)
    funding = get_funding_rates(symbol)
    oi      = get_open_interest(symbol)
    liq     = get_liquidations(symbol)
    whales  = get_whale_transactions(symbol)
    events  = get_economic_events()

    regime  = calc_regime_score(fg, markets, funding, oi,
                                liq, news, whales, events, symbol)

    return {
        "symbol":        symbol,
        "fear_greed":    fg,
        "global_markets":markets,
        "news":          news,
        "funding":       funding,
        "open_interest": oi,
        "liquidations":  liq,
        "whale_txns":    whales,
        "economic_events":events,
        "regime":        regime,
        "high_impact_event": len(events) > 0,
        "timestamp":     datetime.now(timezone.utc).strftime("%H:%M UTC"),
    }


# ── Intelligence bonus score for trading ─────────────────────────────────

def intelligence_score(intel: dict, side: str) -> int:
    """0-15 bonus pts added to trade confidence from regime score."""
    if not intel:
        return 0
    regime_score = intel.get("regime",{}).get("score", 50)
    is_buy = side == "BUY"
    if is_buy and regime_score >= 65:
        return min(15, int((regime_score - 50) / 3))
    if not is_buy and regime_score <= 35:
        return min(15, int((50 - regime_score) / 3))
    return 0
