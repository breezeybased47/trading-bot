# Course → Bot map

How each lesson in the *Learn-Plan-Profit* curriculum maps onto the bot. The
course is mostly **risk management, market context, and psychology** — which is
the durable kind of edge — and a lot of it is already enforced in code. This doc
turns the curriculum into concrete features, config, and "what's still needed".

Legend: ✅ already in the bot · 🆕 built from the course · 🧠 discipline the journal
captures · ❓ needs the exact rules from you.

| Lesson | What it teaches | In the bot |
|---|---|---|
| Intro to risk management | Risk first, always | ✅ `risk_manager.py` + `position_sizer.py` (constant $-risk) |
| Risk-to-reward calculator | Size by R:R | ✅ vol-adjusted sizing targets a fixed dollar-risk per trade (`SIZING_MODEL="vol_adjusted"`) |
| Risk of holding overnight | Don't carry overnight | ✅ intraday only; leveraged ETFs force-closed 15:45 (`LEVERAGED_ETF_CLOSE_TIME`) |
| The risk to carry an open position | Open = exposed | ✅ hard stop 2%, trailing stop, daily-loss halt 5% |
| How to scale as a trader | Bank partial profits | ✅ Module 6 scaling (`SCALING_ENABLED`): +1.5% sell 50%→breakeven, +3% sell 25% |
| Penny / OTC stocks | Avoid illiquid junk | ✅ mega-cap-only universe + `liquidity_guard.py` (spread/volume) |
| Economic reports · calendar · external factors | Don't trade into releases | 🆕 `economic_calendar.py` (`ECON_GUARD_ENABLED`) |
| FOMC meetings | Fed days are landmines | 🆕 Economic Event Guard — FOMC gets a 90-min tail |
| Inflation reports (CPI) | CPI moves everything | 🆕 set `ECON_CPI_DAY` (or paste into `ECON_EVENTS`) |
| Leverage ETFs · path dependencies | 3x ETFs decay in chop | 🆕 `leverage_guard.py` (`LEVERAGED_ETF_REGIME_GUARD_ENABLED`) — no TQQQ/SQQQ in CHOPPY/VOLATILE |
| Introduction to ETFs & the risk | ETF mechanics/risk | ✅ ETF rotation logic + the new leverage guard |
| Waiting for confirmation | Don't anticipate | 🆕 `confirmation.py` overlay + baked into the reversal's break-and-retest (paper challenger) |
| 3 stages of a reversal | Break-and-retest entry | 🆕 `reversal_strategy.py` — rejection → consolidation → break of resistance + retest hold = BUY (paper challenger) |
| Overbought reversals (short) | Short overbought + active sell-off | 🆕 `overbought_reversal.py` — RSI>70 + >upper BB, then rolls over = paper SHORT. (InvestingPro "fair value" = paid, NOT integrated; bot is long-only so this is paper-only) |
| Factors that impact stocks | Context awareness | ✅ regime filter + correlation guard tag/measure context |
| Planning your trades | Trade a plan | 🧠 journal records the plan-context (regime, signals, size) of every trade |
| Quality trades | Be selective | 🧠 journal report flags low-quality tag combinations to avoid |
| Every trade is different | No two setups equal | 🧠 each trade fully tagged so you can compare like-for-like |
| How to manage emotions | Discipline | 🧠 the bot removes emotion by construction; adaptive cooldowns stop revenge trades |
| Blind trading challenge tip | Process over outcome | 🧠 journal measures process (tags), not just P&L |

## 🆕 What was built from the course
1. **Economic Event Guard** — `modules/economic_calendar.py`. Around FOMC / CPI / jobs reports it sets **NO-TOUCH** (block) or **CAUTION** (half size). Free: the jobs report (first Friday 8:30) is computed automatically; for FOMC/CPI paste dates into `config.ECON_EVENTS` from any free economic calendar (the skill the course teaches). Turn on with `ECON_GUARD_ENABLED = True`.
2. **Leveraged-ETF decay guard** — `modules/leverage_guard.py`. Blocks TQQQ/SQQQ entries when the regime is CHOPPY / VOLATILE, where daily-rebalance decay (path dependency) hurts. Turn on with `LEVERAGED_ETF_REGIME_GUARD_ENABLED = True`.

Both default OFF, log every block, and A/B-testable like every other guard.

## 🆕 The entry setups (built from the lesson)
From the "3 Stages of a Reversal" lesson, now implemented as a **break-and-retest**
strategy in `reversal_strategy.py`, run as a paper challenger (`reversal`):

1. **Rejection** — price sells off, lower lows, breaks support.
2. **Consolidation** — it settles into a tight, roughly-parallel range.
3. **Confirmation = BUY** — price breaks above the range's resistance, pulls back, and
   the **old resistance holds as new support** (must not sell back off), then turns up.
   Stop just below the new support; target = 2R (the lesson didn't specify a target).

**Timeframe caveat:** the lesson is taught on daily/swing charts; the bot runs it on
1-minute intraday bars (and closes by EOD), so it catches *intraday* break-and-retests
— a faster cousin of the daily setup. A true daily-swing version would need overnight
holds, which conflicts with the bot's design and the course's own no-overnight lesson.

**Tuning knobs** (in `config.py`): `REVERSAL_LOOKBACK`, `REVERSAL_RANGE_MAX_PCT`,
`REVERSAL_REJECTION_MIN_PCT`, `REVERSAL_RETEST_TOL`, `REVERSAL_TARGET_R`. Tell me where
the course differs (e.g. it uses an SMA filter, a specific range duration) and I'll tune.
