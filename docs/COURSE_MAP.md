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
| Waiting for confirmation | Don't anticipate | ❓ can add a "confirmation" entry mode — needs your exact rule |
| 3 stages of a reversal | A reversal setup | ❓ a new entry strategy — needs the exact mechanical rules |
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

## ❓ What I still need from you — the entry setups
Two lessons describe *entry* setups, but I need their exact mechanics to code them (and I'll build them as a **shadow challenger** so they're A/B-tested before going live):

**"3 stages of a reversal"** — tell me, concretely:
- What defines stage 1 / 2 / 3? (e.g. specific candles, a break of a level, a moving-average cross, volume condition?)
- What's the exact BUY trigger, and what indicators/values?
- Where's the stop, and where's the target/exit?

**"Waiting for confirmation"** — tell me:
- Confirmation of *what*, exactly? (a close above a level? a second candle in the same direction? a retest that holds?)
- How many bars/seconds do you wait, and what cancels the setup?

Describe these however makes sense — even rough — and I'll turn them into a precise spec, then code.
