# Research Modules — what, why, and known limitations

This bot is a **paper-trading research sandbox**. These modules add institutional-style
risk controls, measurement, and research tooling on top of the existing strategy/risk
engine. Two principles hold everywhere:

1. **Nothing here can enable real-money trading.** `PAPER_TRADING` in `config.py` is the
   only switch that does, and no module touches it.
2. **Every powerful feature defaults OFF.** With default config the bot behaves exactly as
   before — modules are opt-in via `config.py` so you can A/B test each one cleanly.

> Honest framing: most of these modules are **risk controls and measurement**, not new
> sources of edge. They make a given edge *safer and measurable* and cut losses to bad
> conditions/fills/correlation — they do not manufacture profit. The edge still lives in
> `strategy.py`.

Run the tests any time: `./venv/bin/python -m unittest discover -s tests -v` (113 tests).

---

## Module 1 — Regime filter (`regime_filter.py`, `market_classifier.py`)
**What:** Classifies the live market (via QQQ) into `TRENDING_UP/DOWN`, `VOLATILE_UP/DOWN`,
`CHOPPY` every 5 min, tracks the bot's own expectancy per regime, and **blocks new entries
in regimes where its history is net-negative**. Half-size until a regime has ≥20 trades of
evidence.
**Theory:** The same setup that prints in a clean trend gets chopped up in a choppy tape.
Not trading your worst regime is often the single biggest, cheapest improvement.
**Key config:** `REGIME_FILTER_ENABLED` (OFF), `REGIME_MIN_SAMPLE`, `REGIME_MIN_EXPECTANCY`.
**Limits:** A fresh classifier (no backtest existed to import); 1-min thresholds are
heuristic and want tuning against logged outcomes. Cold start has no per-regime history, so
it half-sizes rather than blocks.

## Module 2 — Dynamic position sizing (`position_sizer.py`)
**What:** Selectable sizing via `SIZING_MODEL`: `fixed` (10% — current behavior),
`vol_adjusted` (constant dollar-risk; volatile names sized down), `kelly` (half-Kelly from
the bot's own rolling win rate / win-loss ratio). Hard caps: 10% per position, 25% total
exposure, half-size after 2 consecutive losses.
**Theory:** Risk a constant dollar amount regardless of a stock's volatility; bet a little
more only when the measured edge justifies it (fractional Kelly — full Kelly is far too wild).
**Key config:** `SIZING_MODEL` (`fixed`), `ACCOUNT_RISK_PER_TRADE_PCT`, `KELLY_FRACTION`,
`KELLY_MIN_SAMPLE`.
**Limits:** Kelly needs ≥30 trades or it falls back to vol-adjusted. With the default 0.5%
risk + 2% stop, vol-sizing usually hits the 10% cap except on genuinely volatile names.

## Module 3 — Live correlation guard (`correlation_monitor.py`)
**What:** Rolling 30-day and 5-day correlation matrices across the 10 tickers (rebuilt each
morning). Blocks an entry if it correlates >0.70 (30d) or >0.85 (5d) with any open position;
special rule blocks extra long-tech when TQQQ is held.
**Theory:** Three correlated positions are one position at 3× size. The 5-day window catches
"everything selling off together" before the daily-loss limit does.
**Key config:** `CORRELATION_GUARD_ENABLED` (OFF), `CORR_LONG_BLOCK`, `CORR_SHORT_BLOCK`.
**Limits:** Fails open if correlation data is missing (never halts trading on a data hiccup).

## Module 4 — Liquidity & market-impact guard (`liquidity_guard.py`)
**What:** Rejects entries when the bid/ask spread is abnormally wide for that name (tight cap
for AAPL, looser for TQQQ/SQQQ), reduces size in thin conditions, and logs predicted-vs-actual
slippage on every fill.
**Theory:** A good signal filled at a bad price is a bad trade; wide spreads are the free-tier
tell for "bad time to trade this."
**Key config:** `LIQUIDITY_GUARD_ENABLED` (OFF), `SPREAD_MAX_BPS_*`, `DEPTH_*`.
**Limits — important:** Alpaca's free IEX feed has **no Level-2 order book**, so true
"size vs displayed depth" is impossible. The spread guard is the real signal; "depth" is
proxied by top-of-book size and recent 1-min volume, which for these mega-caps essentially
never fires (correct) but catches genuinely thin/halted conditions.

## Module 5 — Adaptive cooldowns + heat (`cooldowns.py`)
**What:** Replaces a flat timeout with outcome-aware cooldowns — clean win 7 min, stop loss
30 min, whipsaw (<3 min) 60 min — doubling per consecutive loss, plus a per-ticker "heat"
score that rises with bad trades and decays over time (more heat = longer cooldown).
**Theory:** Revenge-trading a name that just whipsawed you is a reliable way to bleed; the
cooldown should lengthen exactly when a ticker is misbehaving.
**Key config:** `ADAPTIVE_COOLDOWN_ENABLED` (OFF), `COOLDOWN_*`, `HEAT_*`.
**Limits:** The base bot has no cooldown at all today, so this is purely additive when enabled.

## Module 6 — Partial profit / scaling out (`scaling.py`)
**What:** At +1.5% sell 50% and move the stop to breakeven; at +3% sell another 25% and
tighten the trail on the last 25%. Each partial logged separately.
**Theory:** Banking part of a winner cuts variance hugely while letting a runner run; breakeven
stop makes the trade risk-free after the first scale.
**Key config:** `SCALING_ENABLED` (OFF), `SCALE_TIER1_*`, `SCALE_TIER2_*`.
**Limits:** Can't meaningfully scale tiny positions (≤2 shares). Whether scaling helps *this*
strategy is exactly what the journal/shadow engine will measure.

## Module 7 — Pre-market gap & news scanner (`premarket_scanner.py`)
**What:** Each morning, classifies every ticker: earnings within 24h or a >4% gap with news =
**NO-TOUCH**; a 2–4% gap (or >4% no news) = **CAUTION** (half size); otherwise NORMAL. Emits a
9:15 briefing.
**Theory:** The intraday strategies have no opinion on a stock that gapped on an overnight
catalyst — trading into that is how a clean system gets blindsided.
**Key config:** `PREMARKET_SCANNER_ENABLED` (OFF), `GAP_NOTOUCH_PCT`, `GAP_CAUTION_PCT`,
`FINNHUB_API_KEY`.
**Limits / cost:** Gap detection uses Alpaca data and needs **no signup**. News + earnings
need a **free** Finnhub key in `.env`; without it the scanner runs **gap-only** and says so —
it never blocks and never costs anything.

## Module 8 — Trade journal + auto-analysis (`journal.py`, `journal_report.py`)
**What:** SQLite record of every trade auto-tagged with regime, time-of-day, signals, spread,
sizing model, etc. The report surfaces win rate/expectancy by regime, best/worst time of day,
worst tag *combinations*, and **flags any tag with negative expectancy + enough samples,
suggesting a concrete filter** (with a t-stat so you can tell signal from noise).
**Theory:** The highest-value research output — it tells you *which conditions actually make or
lose money* so filters are driven by evidence, not vibes.
**Key config:** `JOURNAL_ENABLED` (ON — pure logging, safe), `REPORT_MIN_SAMPLE`.
**Limits:** Needs real trades to say anything; the journal is also the data source Modules 9 & 10
depend on.

## Module 9 — Shadow mode / champion vs challenger (`shadow_engine.py`)
**What:** Runs challenger config-variants in parallel on the same live trades (separate thread),
paper-logging hypothetical P&L only — **never sends orders**. Compares champion vs challengers
and *recommends* (never auto-switches) if one wins on risk-adjusted return over a meaningful
sample.
**Theory:** The safe way to evolve a strategy is to let variants compete on live data with zero
risk before you flip anything on.
**Key config:** `SHADOW_ENABLED` (OFF; `RESEARCH_MODE` also activates it), `SHADOW_CHALLENGERS`.
**Limits:** Evaluates variants as a counterfactual on the champion's actual trades, so it best
judges tighter-entry or different-exit variants; it can't invent trades the champion never took.

## Module 10 — ML confirmation filter, VETO-ONLY (`ml_filter.py`)
**What:** A gradient-boosted classifier trained on **your own** logged trades (features = entry
tags, label = did it hit +target before stop). At entry it outputs P(win) and can only **veto**
low-probability setups — it never generates a signal.
**Theory:** Used narrowly as a veto with strict anti-overfitting guards, ML can prune
coin-flip-loser setups without hijacking the strategy.
**Key config:** `ML_FILTER_ENABLED` (OFF), `ML_MIN_TRAIN_TRADES` (300), `ML_VETO_THRESHOLD`.
**Safety:** Time-series CV only (never shuffle), walk-forward retrain, live calibration tracking
that **auto-disables** the model if predicted vs actual win-rate drifts. **Fails open** in every
uncertain case (no model/library/data → allows the trade). Heavy libs (`lightgbm`/`shap`) are
intentionally not installed yet — you have 0 of the 300 trades needed.
**Limits:** Inert until you have hundreds of real trades and `pip install lightgbm shap`.

## Module 11 — Latency & execution-quality monitor (`latency_monitor.py`)
**What:** Timestamps signal→submit→ack→fill, tracks the round-trip latency distribution, alerts
if p95 exceeds a threshold, and — if latency degrades — **widens the exit thresholds** (a slow
bot should be less twitchy).
**Theory:** Sub-second exit logic is meaningless if fills lag; when the bot is slow it should
stop pretending to be fast.
**Key config:** `LATENCY_MONITOR_ENABLED` (ON — measurement only), `LATENCY_P95_ALERT_MS`,
`LATENCY_EXIT_WIDEN_MULT`.
**Limits:** Measures wall-clock around order calls; not a substitute for colocation-grade timing.

## Module 12 — Master control & observability (`config.py`, `structured_log.py`, `log_setup.py`)
**What:** One `config.py` section toggles every module; `structured_log.py` writes one JSON event
per line (`logs/events.jsonl`) so every guard's block/resize is measurable in pandas; a global
kill switch (file `KILL_SWITCH`) flattens everything; `RESEARCH_MODE` turns on max logging + the
shadow engine.
**Theory:** A guard you can't measure is useless; an experiment you can't toggle isn't an
experiment.
**Key config:** `RESEARCH_MODE`, `JSON_LOGGING`, `KILL_SWITCH_FILE`.

---

## Operational note — Alpaca single-connection limit
Alpaca's free data plan allows **one** live websocket connection. If a second instance connects
(e.g. the 24/7 Oracle VM bot **and** the local Mac bot), the second one is rejected with
`connection limit exceeded`. alpaca-py retried this with zero backoff and ballooned
`logs/bot_error.log` to 14 MB. `data_feed.py` now supervises the stream: if it can't connect
within `DATA_CONNECT_TIMEOUT` (30 s) it degrades to **no-feed** with one clear message instead of
looping, and `log_setup.py` de-spams + caps the logs. **Run only one streaming instance.**

## How to turn a module on
Edit `config.py`, flip its `*_ENABLED` flag (or set `SIZING_MODEL`), and restart the bot. Watch
`logs/events.jsonl` to see it act, and the journal report to measure whether it helped.
