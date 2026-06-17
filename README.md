# Trading Bot — Setup Guide

## Prerequisites
- Python 3.11+
- Free Alpaca Markets account (paper trading)

## Step 1 — Clone and install

```bash
git clone <your-repo>
cd trading_bot
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Step 2 — API keys

```bash
cp .env.example .env
```

Edit `.env` and paste your Alpaca paper-trading keys:
```
ALPACA_API_KEY=PKxxxxxxxxxxxxxxxx
ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Get your keys at: https://app.alpaca.markets → Paper Trading → API Keys

## Step 3 — Configure

Edit `config.py` to adjust:
- `PAPER_TRADING = True`   ← keep this True until profitable in paper mode
- `TICKERS`               ← which stocks to trade
- `MAX_POSITION_PCT`      ← position sizing (default 10%)
- `STOP_LOSS_PCT`         ← hard stop (default 2%)
- `DAILY_LOSS_LIMIT_PCT`  ← daily halt threshold (default 5%)

## Step 4 — Run

```bash
python main.py
```

Press `Ctrl+C` to stop cleanly.

## Optional — Telegram alerts

1. Message @BotFather on Telegram → create a bot → copy the token
2. Get your chat ID from @userinfobot
3. Add both to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABCdef...
   TELEGRAM_CHAT_ID=987654321
   ```

## Project structure

```
trading_bot/
├── main.py              # Entry point — wires everything together
├── config.py            # All settings — edit here
├── requirements.txt
├── .env                 # Your API keys (never commit this)
├── .env.example         # Template
├── logs/
│   └── trades.csv       # Auto-generated trade history
└── modules/
    ├── data_feed.py     # Alpaca websocket + historical data
    ├── indicators.py    # RSI, MACD, EMA, VWAP, Bollinger Bands
    ├── strategy.py      # Momentum, mean reversion, ETF rotation
    ├── risk_manager.py  # Stops, position limits, daily loss halt
    ├── broker.py        # Alpaca order execution
    ├── dashboard.py     # Rich terminal UI
    ├── trade_logger.py  # CSV logging + stats
    └── alerts.py        # Telegram notifications
```

## WARNING

This bot trades real (or paper) money automatically.
- **Always start in paper trading mode** (`PAPER_TRADING = True`)
- **Never set `PAPER_TRADING = False`** until you have weeks of profitable paper results
- Past performance of any strategy does not guarantee future results
- Leveraged ETFs (TQQQ/SQQQ) are high-risk instruments
