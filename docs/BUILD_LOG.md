# Build log — research upgrade

A plain-English record of what was built, why, and where it stands. Detail lives in
[MODULES.md](MODULES.md) (the 12 institutional modules) and [COURSE_MAP.md](COURSE_MAP.md)
(how the trading course maps to the bot). This file is the high-level summary.

Everything is a **paper-trading research sandbox** — `PAPER_TRADING=True` is enforced
and never touched. Powerful features default OFF; measurement features default ON.

## Status (as of 2026-06-18)
- **Live on the Oracle VM** (`ubuntu@163.192.2.198`, systemd `trading-bot.service`),
  running clean (no crash loop), PAPER mode, journal + shadow + paper challengers collecting data.
- The **local Mac bot** runs degraded as a viewer/dashboard only (Oracle is the sole
  data streamer — Alpaca's free plan allows one live connection).
- 160 unit tests, all green (`./venv/bin/python -m unittest discover -s tests`).

## What was built

### Institutional research modules (PR #2)
1. **Regime filter** — blocks entries in market regimes where the bot's own history loses money.
2. **Position sizer** — fixed / volatility-adjusted / fractional-Kelly, with 10%/25% caps.
3. **Correlation guard** — blocks piling into names that move together.
4. **Liquidity guard** — rejects wide-spread fills; logs real slippage (free-tier proxy).
5. **Adaptive cooldowns + heat** — longer timeouts after bad trades / whipsaws.
6. **Scaling out** — bank 50% at +1.5% (stop→breakeven), 25% at +3%.
7. **Pre-market scanner** — gap/news/earnings no-touch list (free gap detection; optional Finnhub).
8. **Trade journal + weekly report** — SQLite, rich auto-tags; flags negative-expectancy conditions.
9. **Shadow engine** — champion-vs-challenger A/B on live data, paper-logged.
10. **ML veto filter** — veto-only scaffold; inert until ~300 trades exist.
11. **Latency monitor** — fill-speed tracking; widens exits if slow.
12. **Master config + structured logging + kill switch** (`KILL_SWITCH` file flattens everything).

### Course-derived additions (PR #3)
- **Economic Event Guard** — pause/shrink around FOMC / CPI / jobs reports (free schedules).
- **Leveraged-ETF decay guard** — no TQQQ/SQQQ in CHOPPY/VOLATILE regimes (path dependency).
- **Reversal strategy** (paper) — the course's break-and-retest: rejection → consolidation →
  break of resistance + retest hold = BUY.
- **Confirmation overlay** (paper) — wait for the next bar to confirm before entering.
- **Overbought-reversal short** (paper) — short RSI-overbought, over-extended names as they roll over.
- All three run as **paper challengers** (never real orders) via the paper engine.

### Operational fixes
- **Connection-limit bleed-stop** — stream watchdog + log de-spam (was filling a 14 MB error log).
- **Position reconciliation** — `sync_from_broker()` adopts existing account positions on startup so
  `MAX_OPEN_POSITIONS` actually holds across restarts.
- **Cancel-stale spam fix (PR #4)** — filled orders leave the pending set; "already filled" cancels
  are treated as benign.

## How to use it
- **Turn a guard on:** edit its flag in `config.py`, restart the bot. A/B one at a time.
- **Weekly research report:**
  `ssh ubuntu@163.192.2.198 'cd ~/trading_bot && venv/bin/python -c "from modules import journal_report as r; print(r.text_report())"'`
- **Emergency stop:** `touch KILL_SWITCH` in the bot folder.
- **Deploy an update:** merge the PR, then on Oracle:
  `cd ~/trading_bot && git pull --ff-only origin main && sudo systemctl restart trading-bot.service`

## Open items
- Rotate the Alpaca **paper** API keys (they're in git history) and update `.env`.
- ML filter stays inert until ~300 journaled trades + `pip install lightgbm shap`.
- Tune the course strategies once real A/B data accrues; feed in more lessons as desired.
- Optional: SSH tunnel to view Oracle's live dashboard at `:5001`.

## The honest bottom line
These modules are **risk controls + measurement**, not new profit. The edge lives in the
entry/exit logic. The journal + paper challengers are now gathering the evidence to show —
with data, not opinion — which conditions and which strategies actually work on this universe.
Let it run, read the weekly report, then enable one thing at a time.
