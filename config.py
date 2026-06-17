import os
from dotenv import load_dotenv

load_dotenv()

# ─── MODE ─────────────────────────────────────────────────────────────────────
PAPER_TRADING = True        # Flip to False only when you're ready for real money

# ─── API KEYS (never hardcode — loaded from .env) ─────────────────────────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")   # optional
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")     # optional

# ─── ALPACA ENDPOINTS ─────────────────────────────────────────────────────────
ALPACA_BASE_URL = (
    "https://paper-api.alpaca.markets" if PAPER_TRADING
    else "https://api.alpaca.markets"
)

# ─── TRADING UNIVERSE ─────────────────────────────────────────────────────────
TICKERS = [
    "TQQQ", "SQQQ",
    "NVDA", "TSLA", "AAPL",
    "MSFT", "META", "GOOGL",
    "AMD",  "AMZN"
]
LEVERAGED_ETFS = ["TQQQ", "SQQQ"]
QQQ_TICKER = "QQQ"          # Used for NASDAQ trend detection

# ─── MARKET HOURS (ET) ────────────────────────────────────────────────────────
MARKET_OPEN              = "09:30"
MARKET_CLOSE             = "16:00"
LEVERAGED_ETF_CLOSE_TIME = "15:45"  # Force-close all leveraged ETFs by 3:45 PM

# ─── INDICATOR SETTINGS ───────────────────────────────────────────────────────
RSI_PERIOD   = 14
RSI_BUY      = 55       # RSI crosses above this → momentum buy signal
RSI_SELL     = 45       # RSI drops below this  → momentum sell signal
RSI_OVERSOLD = 30       # RSI below this        → mean reversion buy setup
MACD_FAST    = 12
MACD_SLOW    = 26
MACD_SIGNAL  = 9
EMA_SHORT    = 9
EMA_MED      = 21
EMA_LONG     = 50
BB_PERIOD    = 20
BB_STD       = 2.0
MIN_CANDLES  = 55       # Warm-up bars required before generating any signal

# ─── RISK MANAGEMENT ──────────────────────────────────────────────────────────
MAX_POSITION_PCT        = 0.10   # Max 10% of portfolio per trade
MAX_OPEN_POSITIONS      = 3      # Never more than 3 concurrent positions
STOP_LOSS_PCT           = 0.02   # Hard stop: 2% below entry
TRAILING_STOP_PCT       = 0.015  # Trailing stop: 1.5% below peak
TRAILING_STOP_TRIGGER   = 0.01   # Activate trailing stop when position is up 1%
DAILY_LOSS_LIMIT_PCT    = 0.05   # Halt all trading if portfolio drops 5% today
UNFILLED_ORDER_TIMEOUT  = 60     # Cancel orders still unfilled after 60 seconds

# ─── LOGGING ──────────────────────────────────────────────────────────────────
TRADES_LOG_FILE = "logs/trades.csv"
