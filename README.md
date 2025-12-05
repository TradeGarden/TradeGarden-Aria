# TradeGarden — Aria Trading Engine

Aria is an AI-powered trading engine designed for:
- Daily market analysis
- Automated trading using Alpaca API
- Strict 2% risk management
- Clear reasoning for every trade
- Post-trade analysis for learning and improvement

## Features
- Uses Alpaca paper/live trading
- Reads rules from `trade_rules.md`
- JSON-formatted trade instructions
- Built to run on Render or any cloud server
- Safe and rule-based trading only

## Files
- `main.py` — Core trading engine
- `trade_rules.md` — Risk rules & allowed behavior
- `requirements.txt` — Dependencies for deployment

## How it works
Aria monitors market conditions and produces:
- Trade signal  
- Reasoning  
- Stop-loss  
- Take-profit  
- Risk %  
- JSON output  

This ensures consistent, rule-based trading.
