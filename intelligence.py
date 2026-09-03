"""
intelligence.py - Aria Intelligence Engine
==========================================
Fixed data sources - all verified to work from Railway.

Working sources:
  - Fear & Greed: alternative.me (reliable)
  - Funding rates: Binance futures with correct headers
  - Open Interest: Binance futures
  - Long/Short: Binance futures
  - Liquidations: Binance futures allForceOrders
  - Price/Volume: Kraken (already proven working)
  - DXY: Stooq with fixed parsing
  - Global markets: CoinGecko
  - News: CryptoPanic + RSS feeds
  - Whales: Whale Alert RSS
  - Events: TradingEconomics RSS
"""

import requests
from datetime import datetime, timezone

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
T = 12


def _get(url, params=None, extra_headers=None):
    h = {**HEADERS, **(extra_headers or {})}
    try:
        r = requests.get(url, params=params, headers=h, timeout=T)
        if r.status_code in (429, 403, 503, 451):
            return {}
        r.raise_for_status()
        ct = r.headers.get("content-type","")
        if "json" in ct or r.text.strip().startswith("{") or r.text.strip().startswith("["):
            return r.json()
        return {}
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


# ── Fear & Greed ───────────────────────────────────────────────

def get_fear_greed() -> dict:
    try:
        data  = _get("https://api.alternative.me/fng/?limit=7")
        items = data.get("data", [])
        if items:
            cur  = items[0]
            val  = int(cur["value"])
            hist = [int(x["value"]) for x in items]
            prev = hist[1] if len(hist) > 1 else val
            return {
                "value":     val,
                "label":     cur["value_classification"],
                "change":    val - prev,
                "history":   hist,
                "signal":    ("EXTREME_FEAR"  if val <= 25 else
                              "FEAR"          if val <= 45 else
                              "NEUTRAL"       if val <= 55 else
                              "GREED"         if val <= 75 else
                              "EXTREME_GREED"),
                "available": True,
            }
    except Exception:
        pass
    return {"value":50,"label":"N/A","change":0,"history":[],"signal":"NEUTRAL","available":False}


# ── Binance Futures (funding, OI, L/S, liquidations) ──────────

def _binance_futures(endpoint, params):
    """Binance futures with proper headers."""
    base = "https://fapi.binance.com"
    extra = {
        "Referer": "https://www.binance.com/",
        "Origin":  "https://www.binance.com",
    }
    try:
        r = requests.get(f"{base}{endpoint}", params=params,
                         headers={**HEADERS, **extra}, timeout=T)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def get_funding_rate(symbol: str = "BTCUSD") -> dict:
    pair = "BTCUSDT" if "BTC" in symbol else "ETHUSDT"
    data = _binance_futures("/fapi/v1/premiumIndex", {"symbol": pair})
    if data and isinstance(data, dict):
        rate = float(data.get("lastFundingRate", 0))
        mark = float(data.get("markPrice", 0))
        idx  = float(data.get("indexPrice", 0))
        return {
            "rate":       round(rate * 100, 4),
            "annualized": round(rate * 3 * 365 * 100, 1),
            "mark_price": round(mark, 2),
            "index_price":round(idx, 2),
            "signal": ("OVERHEATED_LONGS"  if rate >  0.0005 else
                       "LONGS_DOMINANT"    if rate >  0.0001 else
                       "OVERHEATED_SHORTS" if rate < -0.0005 else
                       "SHORTS_DOMINANT"   if rate < -0.0001 else
                       "NEUTRAL"),
            "available": True,
        }
    return {"rate":0,"annualized":0,"mark_price":0,"index_price":0,"signal":"NEUTRAL","available":False}


def get_open_interest(symbol: str = "BTCUSD") -> dict:
    pair = "BTCUSDT" if "BTC" in symbol else "ETHUSDT"
    d1   = _binance_futures("/fapi/v1/openInterest", {"symbol": pair})
    d2   = _binance_futures("/futures/data/openInterestHist",
                            {"symbol": pair, "period": "1h", "limit": 25})
    if d1 and isinstance(d1, dict):
        oi    = float(d1.get("openInterest", 0))
        price_data = _binance_futures("/fapi/v1/ticker/price", {"symbol": pair})
        price = float(price_data.get("price", 1)) if price_data else 1
        oi_usd = oi * price

        hist = []
        chg  = 0
        if d2 and isinstance(d2, list):
            hist = [float(x.get("sumOpenInterest", 0)) * price / 1e9 for x in d2[-25:]]
            if len(hist) >= 2 and hist[0] > 0:
                chg = round((hist[-1] - hist[0]) / hist[0] * 100, 2)

        return {
            "value_usd":  round(oi_usd / 1e9, 2),
            "change_24h": chg,
            "history":    hist,
            "signal": ("STRONG_EXPANSION"   if chg >  5 else
                       "EXPANDING"          if chg >  2 else
                       "STRONG_CONTRACTION" if chg < -5 else
                       "CONTRACTING"        if chg < -2 else
                       "STABLE"),
            "available": True,
        }
    return {"value_usd":0,"change_24h":0,"history":[],"signal":"STABLE","available":False}


def get_long_short(symbol: str = "BTCUSD") -> dict:
    pair = "BTCUSDT" if "BTC" in symbol else "ETHUSDT"
    data = _binance_futures("/futures/data/globalLongShortAccountRatio",
                            {"symbol": pair, "period": "1h", "limit": 24})
    if data and isinstance(data, list) and len(data) > 0:
        cur   = data[-1]
        ratio = float(cur.get("longShortRatio", 1))
        longs = round(ratio / (1 + ratio) * 100, 1)
        hist  = [round(float(x.get("longShortRatio",1))/(1+float(x.get("longShortRatio",1)))*100,1)
                 for x in data]
        return {
            "longs":    longs,
            "shorts":   round(100 - longs, 1),
            "ratio":    round(ratio, 3),
            "history":  hist,
            "signal": ("CROWDED_LONGS"  if longs > 65 else
                       "CROWDED_SHORTS" if longs < 35 else
                       "BALANCED"),
            "available": True,
        }
    return {"longs":50,"shorts":50,"ratio":1,"history":[],"signal":"BALANCED","available":False}


def get_liquidations(symbol: str = "BTCUSD") -> dict:
    pair = "BTCUSDT" if "BTC" in symbol else "ETHUSDT"
    data = _binance_futures("/fapi/v1/allForceOrders", {"symbol": pair, "limit": 200})
    if data and isinstance(data, list):
        longs  = sum(float(o.get("origQty",0)) * float(o.get("price",0))
                    for o in data if o.get("side") == "SELL")
        shorts = sum(float(o.get("origQty",0)) * float(o.get("price",0))
                    for o in data if o.get("side") == "BUY")
        return {
            "longs_usd":  round(longs / 1e6, 2),
            "shorts_usd": round(shorts / 1e6, 2),
            "signal": ("BEARISH" if longs > shorts * 1.5
                       else "BULLISH" if shorts > longs * 1.5
                       else "NEUTRAL"),
            "available": True,
        }
    return {"longs_usd":0,"shorts_usd":0,"signal":"NEUTRAL","available":False}


# ── Coin Price Data (Kraken - already proven working) ──────────

def get_coin_data(symbol: str = "BTCUSD") -> dict:
    """Use Kraken ticker - proven to work on Railway."""
    kraken_pairs = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD"}
    pair = kraken_pairs.get(symbol, "XBTUSD")
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": pair}, headers=HEADERS, timeout=T,
        )
        if r.status_code == 200:
            result = r.json().get("result", {})
            key    = list(result.keys())[0] if result else None
            if key:
                d = result[key]
                price  = float(d["c"][0])
                open_  = float(d["o"])
                high   = float(d["h"][1])
                low    = float(d["l"][1])
                vol    = float(d["v"][1])
                chg    = round((price - open_) / open_ * 100, 2) if open_ else 0
                return {
                    "price":      price,
                    "change_24h": chg,
                    "volume_24h": round(vol * price / 1e9, 2),
                    "high_24h":   high,
                    "low_24h":    low,
                    "market_cap": 0,
                    "available":  True,
                }
    except Exception:
        pass
    return {"price":0,"change_24h":0,"volume_24h":0,"high_24h":0,
            "low_24h":0,"market_cap":0,"available":False}


# ── Global Markets (CoinGecko) ─────────────────────────────────

def get_global_markets() -> dict:
    out = {"btc_dominance":0,"eth_dominance":0,"total_mcap":0,
           "mcap_change":0,"dxy":0,"dxy_change":0,"dxy_available":False,"available":False}
    try:
        data = _get("https://api.coingecko.com/api/v3/global")
        d    = data.get("data", {})
        if d:
            out["btc_dominance"] = round(d.get("market_cap_percentage",{}).get("btc",0),1)
            out["eth_dominance"] = round(d.get("market_cap_percentage",{}).get("eth",0),1)
            out["total_mcap"]    = round(d.get("total_market_cap",{}).get("usd",0)/1e12,2)
            out["mcap_change"]   = round(d.get("market_cap_change_percentage_24h_usd",0),2)
            out["available"]     = True
    except Exception:
        pass

    # DXY from Stooq - fixed parsing
    try:
        r = requests.get(
            "https://stooq.com/q/l/?s=dxy&f=sd2t2ohlcv&h&e=csv",
            headers=HEADERS, timeout=T,
        )
        text  = r.text.strip()
        lines = [l.strip() for l in text.split("\n") if l.strip() and "," in l]
        # Skip header line
        data_lines = [l for l in lines if not l.startswith("Symbol") and not l.startswith("Date")]
        if data_lines:
            parts = data_lines[-1].split(",")
            if len(parts) >= 5:
                try:
                    price = float(parts[4])
                    open_ = float(parts[2])
                    if 70 < price < 130:
                        out["dxy"]           = round(price, 2)
                        out["dxy_change"]    = round(price - open_, 3)
                        out["dxy_available"] = True
                except (ValueError, IndexError):
                    pass
    except Exception:
        pass

    return out


# ── News ───────────────────────────────────────────────────────

def get_news(symbol: str = "BTCUSD", limit: int = 20) -> list:
    coin      = "BTC" if "BTC" in symbol else "ETH"
    coin_full = "bitcoin" if coin == "BTC" else "ethereum"
    news      = []

    # CryptoPanic
    try:
        r = requests.get(
            "https://cryptopanic.com/api/free/v1/posts/",
            params={"auth_token":"free","currencies":coin,
                    "kind":"news","public":"true"},
            headers=HEADERS, timeout=T,
        )
        if r.status_code == 200:
            for p in r.json().get("results",[])[:15]:
                votes = p.get("votes",{})
                bull  = votes.get("positive",0)
                bear  = votes.get("negative",0)
                sent  = ("BULLISH" if bull > bear*1.2
                         else "BEARISH" if bear > bull*1.2
                         else "NEUTRAL")
                news.append({
                    "title":     p.get("title",""),
                    "url":       p.get("url",""),
                    "source":    p.get("source",{}).get("title",""),
                    "sentiment": sent,
                    "important": bool(p.get("is_important")),
                    "published": p.get("published_at","")[:16],
                })
    except Exception:
        pass

    # CoinTelegraph RSS
    if len(news) < 5:
        for item in _rss("https://cointelegraph.com/rss", 20):
            title = item.get("title","")
            if coin.lower() in title.lower() or coin_full in title.lower():
                news.append({
                    "title":title,"url":item.get("link",""),
                    "source":"CoinTelegraph","sentiment":"NEUTRAL",
                    "important":False,"published":item.get("pubDate","")[:16],
                })

    # CoinDesk RSS
    if len(news) < 8:
        for item in _rss("https://www.coindesk.com/arc/outboundfeeds/rss/", 15):
            title = item.get("title","")
            if coin.lower() in title.lower() or coin_full in title.lower():
                news.append({
                    "title":title,"url":item.get("link",""),
                    "source":"CoinDesk","sentiment":"NEUTRAL",
                    "important":False,"published":item.get("pubDate","")[:16],
                })

    seen, out = set(), []
    for n in news:
        k = n["title"][:40]
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out[:limit]


# ── Whales ─────────────────────────────────────────────────────

def get_whales(symbol: str = "BTCUSD") -> list:
    coin   = "BTC" if "BTC" in symbol else "ETH"
    whales = []
    for item in _rss("https://whale-alert.io/rss/all", 30):
        title = item.get("title","")
        if not title:
            continue
        tu = title.upper()
        if coin not in tu and \
           ("BITCOIN" not in tu or coin != "BTC") and \
           ("ETHEREUM" not in tu or coin != "ETH"):
            continue
        tl = title.lower()
        direction = (
            "TO_EXCHANGE"   if any(x in tl for x in
                ["to coinbase","to binance","to kraken","to okx","to bybit","to exchange"]) else
            "FROM_EXCHANGE" if any(x in tl for x in
                ["from coinbase","from binance","from kraken","from okx","from bybit","from exchange"]) else
            "WALLET_TO_WALLET"
        )
        signal = ("BEARISH" if direction=="TO_EXCHANGE"
                  else "BULLISH" if direction=="FROM_EXCHANGE"
                  else "NEUTRAL")
        whales.append({
            "title":title,"url":item.get("link",""),
            "direction":direction,"signal":signal,
            "published":item.get("pubDate","")[:16],
        })
    return whales[:12]


# ── Economic Events ────────────────────────────────────────────

def get_events() -> list:
    HIGH = ["CPI","FOMC","NFP","GDP","PPI","INTEREST RATE","FED","POWELL",
            "INFLATION","UNEMPLOYMENT","PAYROLL","TARIFF","TRUMP",
            "CRYPTO","SEC","ETF","BITCOIN","RATE DECISION"]
    events = []
    for item in _rss("https://tradingeconomics.com/rss/news.aspx", 25):
        title = item.get("title","")
        if any(k in title.upper() for k in HIGH):
            events.append({
                "title":title,"url":item.get("link",""),
                "source":"TradingEconomics",
                "published":item.get("pubDate","")[:16],
            })
    seen, out = set(), []
    for e in events:
        k = e["title"][:40]
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out[:6]


# ── Regime Score ───────────────────────────────────────────────

def calc_regime(fg, markets, funding, oi, liq, ls, news, whales, coin_data) -> dict:
    score=50; drivers=[]; risks=[]

    if fg.get("available"):
        v = fg["value"]
        if v<=25:   score+=12; drivers.append(f"Extreme Fear ({v}) — contrarian buy signal")
        elif v<=45: score+=5;  drivers.append(f"Fear ({v}) — mild bullish bias")
        elif v>=76: score-=12; risks.append(f"Extreme Greed ({v}) — reversal risk high")
        elif v>=56: score-=5;  risks.append(f"Greed ({v}) — elevated caution")

    if markets.get("available"):
        c = markets["mcap_change"]
        if c>5:    score+=10; drivers.append(f"Market expanding strongly +{c:.1f}%")
        elif c>2:  score+=4;  drivers.append(f"Market growing +{c:.1f}%")
        elif c<-5: score-=10; risks.append(f"Market contracting {c:.1f}%")
        elif c<-2: score-=4;  risks.append(f"Market declining {c:.1f}%")

    if markets.get("dxy_available"):
        dc = markets["dxy_change"]
        if dc<-0.3:  score+=8;  drivers.append(f"DXY weakening ({dc:+.2f}) — risk-on for crypto")
        elif dc<-0.1:score+=3
        elif dc>0.3: score-=8;  risks.append(f"DXY strengthening ({dc:+.2f}) — risk-off")
        elif dc>0.1: score-=3

    if coin_data.get("available"):
        c24 = coin_data["change_24h"]
        if c24>3:   score+=6;  drivers.append(f"Strong momentum +{c24:.1f}%")
        elif c24>1: score+=2
        elif c24<-3:score-=6;  risks.append(f"Selling pressure {c24:.1f}%")
        elif c24<-1:score-=2

    if funding.get("available"):
        fs = funding["signal"]
        if fs=="OVERHEATED_LONGS":  score-=10; risks.append("Funding overheated — long flush risk")
        elif fs=="LONGS_DOMINANT":  score-=4;  risks.append("Longs crowded in funding")
        elif fs=="OVERHEATED_SHORTS":score+=10; drivers.append("Shorts overloaded — squeeze potential")
        elif fs=="SHORTS_DOMINANT": score+=4;  drivers.append("Shorts crowded — squeeze risk")

    if oi.get("available"):
        os_ = oi["signal"]
        if os_=="STRONG_EXPANSION":  score+=8; drivers.append("OI expanding strongly — new money in")
        elif os_=="EXPANDING":       score+=3; drivers.append("Open interest growing")
        elif os_=="STRONG_CONTRACTION":score-=8; risks.append("OI collapsing — capital exiting")
        elif os_=="CONTRACTING":     score-=3; risks.append("Open interest shrinking")

    if liq.get("available"):
        ls2 = liq["signal"]
        if ls2=="BULLISH": score+=5; drivers.append(f"Shorts liquidated ${liq['shorts_usd']:.1f}M")
        elif ls2=="BEARISH":score-=5; risks.append(f"Longs liquidated ${liq['longs_usd']:.1f}M")

    if ls.get("available"):
        lp = ls["longs"]
        if lp>65:  score-=5; risks.append(f"Retail over-long ({lp:.0f}%)")
        elif lp<35:score+=5; drivers.append(f"Retail over-short — squeeze risk")

    bull_n = sum(1 for n in news if n.get("sentiment")=="BULLISH")
    bear_n = sum(1 for n in news if n.get("sentiment")=="BEARISH")
    if bull_n>bear_n+2: pts=min(8,bull_n*2); score+=pts; drivers.append(f"News bullish ({bull_n} positive)")
    elif bear_n>bull_n+2: pts=min(8,bear_n*2); score-=pts; risks.append(f"News bearish ({bear_n} negative)")

    bull_w = sum(1 for w in whales if w.get("signal")=="BULLISH")
    bear_w = sum(1 for w in whales if w.get("signal")=="BEARISH")
    if bull_w>bear_w: score+=min(6,bull_w*2); drivers.append(f"{bull_w} whales accumulating")
    elif bear_w>bull_w: score-=min(6,bear_w*2); risks.append(f"{bear_w} whales selling to exchanges")

    score = max(0,min(100,score))
    regime = ("STRONGLY BULLISH" if score>=72 else "BULLISH" if score>=57
              else "NEUTRAL" if score>=44 else "BEARISH" if score>=29
              else "STRONGLY BEARISH")
    color  = ("#2ecc71" if score>=57 else "#e74c3c" if score<=43 else "#888")

    return {
        "score":score,"regime":regime,"color":color,
        "confidence":min(95,50+abs(score-50)),
        "primary":drivers[0] if drivers else "No strong signal",
        "risk":risks[0] if risks else "No major risks",
        "bull_factors":drivers[:5],"bear_factors":risks[:5],
    }


# ── Full Snapshot ──────────────────────────────────────────────

def get_intelligence(symbol: str = "BTCUSD") -> dict:
    symbol    = symbol.upper()
    fg        = get_fear_greed()
    markets   = get_global_markets()
    coin      = get_coin_data(symbol)
    funding   = get_funding_rate(symbol)
    oi        = get_open_interest(symbol)
    ls        = get_long_short(symbol)
    liq       = get_liquidations(symbol)
    news      = get_news(symbol, 20)
    whales    = get_whales(symbol)
    events    = get_events()
    regime    = calc_regime(fg,markets,funding,oi,liq,ls,news,whales,coin)
    return {
        "symbol":symbol,"fg":fg,"markets":markets,"coin":coin,
        "funding":funding,"oi":oi,"ls":ls,"liq":liq,
        "news":news,"whales":whales,"events":events,"regime":regime,
        "timestamp":datetime.now(timezone.utc).strftime("%H:%M UTC"),
    }
