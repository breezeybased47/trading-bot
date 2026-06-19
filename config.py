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
    "AMD",  "AMZN",
    # Diversifiers across sectors (added 2026-06-18): banks / energy / healthcare / staples / media / industrials
    "JPM", "XOM", "UNH", "WMT", "DIS", "CAT",
]
LEVERAGED_ETFS = ["TQQQ", "SQQQ"]
# The actual tech names — used by the correlation guard's "if TQQQ is open, restrict
# additional long-tech" rule so the non-tech diversifiers above don't get caught by it.
TECH_NAMES = ["NVDA", "TSLA", "AAPL", "MSFT", "META", "GOOGL", "AMD", "AMZN"]

# ─── Leveraged ETFs: stop trading them; use the NASDAQ trend as a direction gauge ──
TRADE_LEVERAGED_ETFS = False   # block NEW TQQQ/SQQQ entries (existing positions still exit)
# Long-term market-direction filter — from QQQ's daily trend (TQQQ/SQQQ are just 3x QQQ,
# so QQQ is the same signal without the leverage noise):
MARKET_DIRECTION_ENABLED = True   # gauge BULL / BEAR / NEUTRAL from QQQ's 50/200-day trend
MARKET_BEAR_SIZE_MULT    = 0.5    # in a confirmed long-term DOWNtrend, size longs at this fraction
MARKET_SMA_FAST          = 50     # daily SMA (intermediate trend)
MARKET_SMA_SLOW          = 200    # daily SMA (long-term trend)
QQQ_TICKER = "QQQ"          # Used for NASDAQ trend detection

# ─── MARKET HOURS (ET) ────────────────────────────────────────────────────────
MARKET_OPEN              = "09:30"
MARKET_CLOSE             = "16:00"
LEVERAGED_ETF_CLOSE_TIME = "15:45"  # Force-close all leveraged ETFs by 3:45 PM
EOD_FLATTEN_ENABLED      = True     # Day-trading: close EVERY position before the bell (no overnight gap risk)
EOD_FLATTEN_TIME         = "15:55"  # Flatten all positions + stop new entries at 3:55 PM ET

# ─── INDICATOR SETTINGS ───────────────────────────────────────────────────────
RSI_PERIOD   = 14
RSI_BUY      = 60       # raised 55→60 (2026-06-18): stricter momentum entry — the rsi60
                        #   challenger was the only profitable variant in the first day's data
RSI_SELL     = 45       # RSI drops below this  → momentum sell signal
RSI_OVERSOLD = 30       # RSI below this        → mean reversion buy setup
RSI_OVERBOUGHT = 70     # RSI above this        → overbought (short-reversal setup)
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

# ─── HOLD DISCIPLINE (stop the rapid churn; ride winners) ─────────────────────
MIN_HOLD_MINUTES      = 10      # ignore a strategy SELL within N min of entry (stops still fire)
# "Bank half, ride half" is handled by the staged SCALING ladder below (sell 50%
# at +2%, 25% at +3%, trail the last 25%). These two are an OPTIONAL hard backstop
# that closes the WHOLE position at a fixed target — left OFF so scaling can ride
# the runner. Set TARGET_PROFIT_DOLLARS>0 if you ever want a hard $ cap instead.
TARGET_PROFIT_PCT     = 0       # hard full-close at this % gain (0 = off; scaling owns the exit)
TARGET_PROFIT_DOLLARS = 0       # ...or a hard full-close at a fixed $ profit (0 = off)

# ─── LOGGING ──────────────────────────────────────────────────────────────────
TRADES_LOG_FILE = "logs/trades.csv"


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  RESEARCH MODULE MASTER CONTROLS                                            ║
# ║  Paper-trading research sandbox. See docs/MODULES.md for the WHY behind    ║
# ║  each module. Every module is independently toggleable for clean A/B       ║
# ║  experiments. Powerful / experimental features DEFAULT TO OFF so the bot   ║
# ║  behaves EXACTLY as before until you deliberately opt in.                  ║
# ║  None of these toggles can re-enable real-money trading — PAPER_TRADING    ║
# ║  above is the only switch that does, and these never touch it.             ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

# ─── GLOBAL (Module 12 — master control & observability) ──────────────────────
RESEARCH_MODE        = True            # Max logging + shadow engine; loosens NO safety
JSON_LOGGING         = True            # Mirror events to structured JSON-lines file
STRUCTURED_LOG_FILE  = "logs/events.jsonl"   # pd.read_json(path, lines=True) later
KILL_SWITCH_FILE     = "KILL_SWITCH"   # If this file appears, flatten everything + halt
DATA_CONNECT_TIMEOUT = 30              # secs to establish Alpaca stream before degrading
                                       # (guards the 'connection limit exceeded' tight loop)

# ─── Module 1 — Regime filter (master switch on entries) ──────────────────────
REGIME_FILTER_ENABLED          = False  # Block entries in historically-negative regimes
REGIME_RECLASSIFY_SECONDS      = 300    # Re-classify the live market every 5 minutes
REGIME_MIN_SAMPLE              = 20     # Trades needed in a regime before trusting it
REGIME_MIN_EXPECTANCY          = 0.0    # Block regime if expectancy ($/trade) < this
REGIME_HALF_SIZE_UNTIL_TRUSTED = True   # Half size in a regime until min sample reached
REGIME_PERF_FILE               = "data/regime_performance.json"  # derived snapshot
# Classifier tuning (modules/market_classifier.py)
REGIME_TREND_LOOKBACK          = 30     # bars used to measure trend direction/strength
REGIME_VOL_LOOKBACK            = 30     # bars used to measure realized volatility
REGIME_VOL_BASELINE_LOOKBACK   = 120    # longer window vol is compared against
REGIME_TREND_THRESHOLD         = 0.003  # |return| over lookback above this = trending
REGIME_VOL_HIGH_MULT           = 1.5    # vol above mult*baseline = "volatile" regime

# ─── Module 2 — Dynamic position sizing ───────────────────────────────────────
SIZING_MODEL               = "fixed"    # "fixed" | "vol_adjusted" | "kelly"
ACCOUNT_RISK_PER_TRADE_PCT = 0.005      # vol model: risk 0.5% of equity per trade
ATR_PERIOD                 = 14
ATR_AVG_PERIOD             = 20         # ATR's own moving average ("20-day average")
ATR_STOP_MULT              = 1.5        # vol-stop distance = mult * ATR
KELLY_FRACTION             = 0.5        # HALF-Kelly (full Kelly is too aggressive)
KELLY_LOOKBACK             = 50         # trades used for win rate / win-loss ratio
KELLY_MIN_SAMPLE           = 30         # below this, kelly falls back to vol_adjusted
MAX_TOTAL_EXPOSURE_PCT     = 0.25       # hard cap on total exposure across positions
CONSEC_LOSS_TRIGGER        = 2          # after N consecutive losses...
CONSEC_LOSS_SIZE_FACTOR    = 0.5        # ...multiply all sizing by this

# ─── Module 3 — Live correlation matrix ───────────────────────────────────────
CORRELATION_GUARD_ENABLED  = True       # ON (combined bot): don't stack correlated names
CORR_LONG_WINDOW_DAYS      = 30
CORR_SHORT_WINDOW_DAYS     = 5
CORR_LONG_BLOCK            = 0.70       # block if 30d corr w/ any open position > this
CORR_SHORT_BLOCK           = 0.85       # block if 5d corr > this (everything-moving-together)
CORR_REFRESH_HOUR_ET       = 8          # rebuild matrix at 8am ET daily
CORR_TQQQ_TECH_RULE        = True       # if TQQQ open, restrict extra long-tech exposure

# ─── Module 4 — Liquidity & market-impact guard (free-tier proxy) ─────────────
LIQUIDITY_GUARD_ENABLED    = True       # ON (combined bot): skip wide-spread / thin entries
SPREAD_MAX_BPS_DEFAULT     = 8          # reject entry if spread wider than N basis points
SPREAD_MAX_BPS_OVERRIDES   = {          # per-ticker (tighter for liquid, looser for ETFs)
    "AAPL": 5, "MSFT": 5, "GOOGL": 6, "AMZN": 6, "META": 6,
    "NVDA": 7, "AMD": 8, "TSLA": 10, "TQQQ": 15, "SQQQ": 15,
}
DEPTH_REJECT_PCT           = 0.15       # reject if size > 15% of (proxy) available depth
DEPTH_REDUCE_PCT           = 0.05       # reduce size if 5-15% of available depth
SLIPPAGE_LOG_FILE          = "data/slippage.jsonl"

# ─── Module 5 — Adaptive cooldowns + heat ─────────────────────────────────────
ADAPTIVE_COOLDOWN_ENABLED  = True       # ON (combined bot): cool down after losses/whipsaws
COOLDOWN_BASE_MIN          = 15
COOLDOWN_CLEAN_WIN_MIN     = 7          # exited via signal (not stop)
COOLDOWN_STOP_LOSS_MIN     = 30         # exited via stop
COOLDOWN_WHIPSAW_MIN       = 60         # entered and stopped within WHIPSAW_SECONDS
WHIPSAW_SECONDS            = 180        # 3 minutes
HEAT_PER_LOSS              = 1.0        # heat added per losing trade on a ticker
HEAT_PER_WHIPSAW           = 2.0
HEAT_DECAY_PER_HOUR        = 0.5        # heat bled off per hour
HEAT_COOLDOWN_MIN_PER_UNIT = 5         # extra cooldown minutes per point of heat

# ─── Module 6 — Partial profit taking / scaling out ───────────────────────────
SCALING_ENABLED            = True       # ON (combined bot): bank partial profits as winners run
SCALE_TIER1_TRIGGER_PCT    = 0.02       # +2% unrealized... ("bank half" target)
SCALE_TIER1_SELL_FRAC      = 0.50       # ...sell 50%, move stop to breakeven on the rest
SCALE_TIER2_TRIGGER_PCT    = 0.03       # +3% unrealized...
SCALE_TIER2_SELL_FRAC      = 0.25       # ...sell another 25%, tighten trailing on last 25%
SCALE_TIER2_TRAIL_PCT      = 0.0075     # tighter trailing stop on the final runner

# ─── Module 7 — Pre-market gap & news scanner ─────────────────────────────────
PREMARKET_SCANNER_ENABLED  = True       # ON (2026-06-18): Finnhub key added; pull news/earnings
PREMARKET_RUN_TIME         = "09:00"    # ET — compute gaps / pull news
PREMARKET_BRIEFING_TIME    = "09:15"    # ET — post briefing to dashboard + Telegram
GAP_NOTOUCH_PCT            = 0.04       # gap > 4% + news = NO-TOUCH for the day
GAP_CAUTION_PCT            = 0.02       # gap 2-4% no news = CAUTION (half size)
NEWS_LOOKBACK_HOURS        = 18
FINNHUB_API_KEY            = os.getenv("FINNHUB_API_KEY")  # OPTIONAL free key; news off if absent

# ─── Module 8 — Trade journal + auto-tagging ──────────────────────────────────
JOURNAL_ENABLED            = True       # pure-additive logging; safe to leave ON
JOURNAL_DB_FILE            = "data/journal.db"
JOURNAL_WIN_TARGET_PCT     = 0.015      # trade labelled win=1 if it reaches +1.5% before stop
REPORT_MIN_SAMPLE          = 8          # min trades in a tag before flagging its expectancy

# ─── Module 9 — Shadow mode (champion / challenger) ───────────────────────────
SHADOW_ENABLED             = False      # RESEARCH_MODE also activates this at runtime
SHADOW_CHALLENGERS = [                   # each overrides champion config; PAPER-LOG ONLY
    {"name": "rsi65",        "RSI_BUY": 65},     # is even stricter better than the new champ (60)?
    {"name": "scaling_on",   "SCALING_ENABLED": True},
    {"name": "regime_on",    "REGIME_FILTER_ENABLED": True},
]

# ─── Module 10 — ML confirmation filter (VETO-ONLY) ───────────────────────────
ML_FILTER_ENABLED          = False      # stays OFF until trade history supports it
ML_MIN_TRAIN_TRADES        = 300        # minimum journaled trades before training
ML_VETO_THRESHOLD          = 0.45       # veto entry if model P(win) < this
ML_MODEL_FILE              = "data/ml_filter.pkl"
ML_CALIBRATION_TOLERANCE   = 0.10       # auto-disable if |predicted - actual| win rate > this
ML_RETRAIN_WEEKDAY         = 6          # 0=Mon .. 6=Sun — walk-forward retrain day

# ─── Module 11 — Latency & execution-quality monitor ──────────────────────────
LATENCY_MONITOR_ENABLED        = True   # pure measurement; safe to leave ON
LATENCY_P95_ALERT_MS           = 1500   # alert if round-trip p95 exceeds this
LATENCY_WIDEN_EXIT_ON_DEGRADE  = True   # slow bot = less twitchy exits
LATENCY_EXIT_WIDEN_MULT        = 1.5    # widen exit thresholds by this when degraded
LATENCY_LOG_FILE               = "data/latency.jsonl"


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  COURSE-DERIVED MODULES  (from the user's risk-management course)          ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

# ─── Economic event guard (FOMC / CPI / jobs reports) ─────────────────────────
# Course lessons: economic reports, economic calendar, FOMC, inflation.
ECON_GUARD_ENABLED         = True      # ON (combined bot): stand down around FOMC/CPI/jobs
ECON_BLACKOUT_BEFORE_MIN   = 15        # blackout starts N min before an event
ECON_BLACKOUT_AFTER_MIN    = 30        # ...and ends N min after a standard release
ECON_FOMC_AFTER_MIN        = 90        # FOMC gets a longer tail (statement + press conf)
ECON_CAUTION_BUFFER_MIN    = 60        # within this window before a blackout -> half size
ECON_AUTO_NFP              = True      # auto-add monthly jobs report (1st Friday, 8:30 ET)
ECON_CPI_DAY               = 0         # day-of-month for CPI 8:30 ET release (0 = off; e.g. 12)
ECON_EVENTS = [                        # high-impact events — paste from a FREE econ calendar
    # ("2026-06-18 14:00", "FOMC", "high"),
    # ("2026-07-15 08:30", "CPI",  "high"),
]

# ─── Leveraged-ETF path-dependency guard ──────────────────────────────────────
# Course lessons: leverage ETFs, path dependencies. TQQQ/SQQQ decay in chop due to
# daily rebalancing, so don't hold them when the market isn't cleanly trending.
LEVERAGED_ETF_REGIME_GUARD_ENABLED = True   # ON (combined bot): no TQQQ/SQQQ in chop/volatile
LEVERAGED_ETF_BAD_REGIMES = ["CHOPPY", "VOLATILE_UP", "VOLATILE_DOWN"]

# ─── Course entry setups: 3-stage reversal + confirmation (PAPER challengers) ──
# These run as independent paper strategies (NO real orders) so we can A/B test
# them against the live champion. Standard interpretations — tune to the course.
PAPER_ENGINE_ENABLED   = True       # run independent strategies in paper (no orders)
PAPER_POSITION_DOLLARS = 10000      # notional per paper trade (fair P&L comparison)
# Reversal = the course's actual "3 stages": rejection (lower lows) -> consolidation
# (tight parallel range) -> CONFIRMATION = break above the range's resistance, then a
# pullback that HOLDS the old resistance as new support (must not sell back off).
REVERSAL_LOOKBACK          = 20     # bars in the consolidation window
REVERSAL_RANGE_MAX_PCT     = 0.03   # range must be <= this fraction of price to be "tight"
REVERSAL_REJECTION_MIN_PCT = 0.02   # must have sold off >= this into the range (the rejection)
REVERSAL_BREAKOUT_BUFFER   = 0.001  # close must clear the resistance by this fraction
REVERSAL_RETEST_TOL        = 0.003  # retest low within this fraction above old resistance
REVERSAL_RETEST_MAX_BARS   = 20     # give up waiting for the retest after N bars
REVERSAL_STOP_BUFFER       = 0.003  # stop just below the new support (old resistance)
REVERSAL_TARGET_R          = 2.0    # profit target = R-multiple (not specified by course; default)
CONFIRMATION_MAX_BARS  = 2          # wait up to N bars for confirmation, else skip
CHAMPION_CONFIRMATION  = True       # apply 'wait for confirmation' to LIVE champion entries
                                    #   (enabled 2026-06-18 — champ_confirmed cut the loss in testing)

# Overbought-reversal SHORT ("overbought reversals" lesson). PAPER ONLY — the live
# bot is long-only; this simulates shorts so the setup can be A/B-studied. (The
# course's InvestingPro "fair value" data is a paid tool and is NOT integrated.)
OVERBOUGHT_LOOKBACK    = 10         # bars to remember "was overbought" before the roll-over
OVERBOUGHT_TARGET_R    = 2.0        # downside target = R-multiple of the risk
OVERBOUGHT_STOP_BUFFER = 0.003      # stop just above the overbought peak
