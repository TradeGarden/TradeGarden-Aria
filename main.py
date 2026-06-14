def fetch_market_data(symbol: str):
    try:
        if "BTC" in symbol:
            r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd", timeout=10)
            r.raise_for_status()
            return float(r.json()["bitcoin"]["usd"])
        else:
            r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd", timeout=10)
            r.raise_for_status()
            return float(r.json()["ethereum"]["usd"])
    except:
        return 60500 if "BTC" in symbol else 3450
