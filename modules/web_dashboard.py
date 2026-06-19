"""
web_dashboard.py
Lightweight Flask web dashboard for the trading bot.
Runs on port 5001 in a background thread.
"""

import logging
import threading
from datetime import datetime

import pytz
from flask import Flask, render_template_string

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, PAPER_TRADING
from modules import dashboard_state

logger = logging.getLogger(__name__)
app = Flask(__name__)
ET = pytz.timezone("America/New_York")

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Trading Bot</title>
  <meta http-equiv="refresh" content="30">
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0d0d0d;color:#e0e0e0;padding:28px;min-height:100vh}
    header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:28px;padding-bottom:20px;border-bottom:1px solid #1e1e1e}
    h1{font-size:18px;font-weight:600;color:#fff;letter-spacing:-0.3px}
    .sub{color:#555;font-size:12px;margin-top:5px}
    .badge{padding:4px 14px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:0.5px;
      background:{% if paper %}#0f2e0f{% else %}#2e0f0f{% endif %};
      color:{% if paper %}#4ade80{% else %}#f87171{% endif %};
      border:1px solid {% if paper %}#1e4d1e{% else %}#4d1e1e{% endif %}}
    .cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:28px}
    .card{background:#111;border:1px solid #1e1e1e;border-radius:10px;padding:18px 20px}
    .card-label{font-size:10px;color:#555;text-transform:uppercase;letter-spacing:0.8px}
    .card-value{font-size:26px;font-weight:700;color:#fff;margin-top:7px;letter-spacing:-0.5px}
    .green{color:#4ade80}.red{color:#f87171}
    section{margin-bottom:28px}
    h2{font-size:11px;font-weight:600;color:#444;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:10px}
    table{width:100%;border-collapse:collapse;background:#111;border:1px solid #1e1e1e;border-radius:10px;overflow:hidden}
    th{text-align:left;font-size:10px;color:#444;text-transform:uppercase;letter-spacing:0.5px;padding:10px 14px;border-bottom:1px solid #1e1e1e;font-weight:500}
    td{padding:12px 14px;border-bottom:1px solid #161616;font-size:13px}
    tr:last-child td{border-bottom:none}
    .sym{font-weight:700;color:#fff;font-size:14px}
    .buy{color:#4ade80;font-weight:600}.sell{color:#f87171;font-weight:600}
    .empty{color:#333;font-style:italic;padding:20px 14px;font-size:13px}
  </style>
</head>
<body>
<header>
  <div>
    <h1>Trading Bot Dashboard</h1>
    <div class="sub">Refreshes every 30s &nbsp;·&nbsp; {{ now }}</div>
  </div>
  <span class="badge">{{ "PAPER" if paper else "LIVE" }}</span>
</header>

<div class="cards">
  <div class="card">
    <div class="card-label">Portfolio Value</div>
    <div class="card-value">${{ portfolio }}</div>
  </div>
  <div class="card">
    <div class="card-label">Buying Power</div>
    <div class="card-value">${{ buying_power }}</div>
  </div>
  <div class="card">
    <div class="card-label">Today's P&amp;L</div>
    <div class="card-value {{ 'green' if pnl >= 0 else 'red' }}">
      {{ '+' if pnl >= 0 else '' }}${{ pnl_str }}
    </div>
  </div>
  <div class="card">
    <div class="card-label">Open Positions</div>
    <div class="card-value">{{ positions|length }}</div>
  </div>
</div>

<section>
  <h2>Open Positions</h2>
  <table>
    <thead><tr>
      <th>Ticker</th><th>Shares</th><th>Entry</th><th>Current</th><th>P&amp;L</th><th>P&amp;L %</th>
    </tr></thead>
    <tbody>
    {% if positions %}
      {% for p in positions %}
      <tr>
        <td class="sym">{{ p.symbol }}</td>
        <td>{{ p.qty }}</td>
        <td>${{ "%.2f"|format(p.avg_entry_price|float) }}</td>
        <td>${{ "%.2f"|format(p.current_price|float) }}</td>
        <td class="{{ 'green' if p.unrealized_pl|float >= 0 else 'red' }}">
          {{ '+' if p.unrealized_pl|float >= 0 else '' }}${{ "%.2f"|format(p.unrealized_pl|float) }}
        </td>
        <td class="{{ 'green' if p.unrealized_plpc|float >= 0 else 'red' }}">
          {{ '+' if p.unrealized_plpc|float >= 0 else '' }}{{ "%.2f"|format(p.unrealized_plpc|float * 100) }}%
        </td>
      </tr>
      {% endfor %}
    {% else %}
      <tr><td class="empty" colspan="6">No open positions right now</td></tr>
    {% endif %}
    </tbody>
  </table>
</section>

<section>
  <h2>Recent Fills</h2>
  <table>
    <thead><tr>
      <th>Time (ET)</th><th>Ticker</th><th>Side</th><th>Shares</th><th>Fill Price</th><th>Total Value</th>
    </tr></thead>
    <tbody>
    {% if orders %}
      {% for o in orders %}
      <tr>
        <td style="color:#555">{{ o.filled_at.astimezone(et).strftime('%b %d  %H:%M') if o.filled_at else '—' }}</td>
        <td class="sym">{{ o.symbol }}</td>
        <td class="{{ 'buy' if o.side.value == 'buy' else 'sell' }}">{{ o.side.value.upper() }}</td>
        <td>{{ o.filled_qty }}</td>
        <td>{% if o.filled_avg_price %}${{ "%.2f"|format(o.filled_avg_price|float) }}{% else %}—{% endif %}</td>
        <td>{% if o.filled_avg_price %}${{ "%.2f"|format(o.filled_qty|float * o.filled_avg_price|float) }}{% else %}—{% endif %}</td>
      </tr>
      {% endfor %}
    {% else %}
      <tr><td class="empty" colspan="6">No fills yet today</td></tr>
    {% endif %}
    </tbody>
  </table>
</section>

{% if research %}
<section>
  <h2>Research Modules {% if research.updated %}<span style="color:#333">· updated {{ research.updated }}</span>{% endif %}</h2>
  <div style="display:flex;flex-wrap:wrap;gap:7px">
    {% for k, v in (research.toggles or {}).items() %}
      <span style="padding:4px 11px;border-radius:14px;font-size:10px;font-weight:600;border:1px solid #1e1e1e;
        background:{% if v and v != 'fixed' %}#0f2e0f{% else %}#161616{% endif %};
        color:{% if v and v != 'fixed' %}#4ade80{% else %}#555{% endif %}">
        {{ k.replace('_ENABLED','').replace('_',' ')|lower }}{% if v in ['fixed','vol_adjusted','kelly'] %}: {{ v }}{% endif %}
      </span>
    {% endfor %}
  </div>
  {% if research.degraded_feed %}<div style="color:#fbbf24;font-size:12px;margin-top:8px">⚠️ data feed degraded on this instance (Oracle VM is the streamer)</div>{% endif %}
</section>

<div class="cards">
  <div class="card">
    <div class="card-label">Market Direction</div>
    <div class="card-value" style="font-size:17px">{{ research.market.direction if research.market else '—' }}</div>
    <div class="sub">
      {%- if research.market and research.market.size_mult and research.market.size_mult != 1.0 %}<span class="red">defensive · size x{{ research.market.size_mult }}</span>
      {%- elif research.market %}QQQ 50/200-day trend{% else %}long-term trend{% endif %}</div>
  </div>
  <div class="card">
    <div class="card-label">Market Regime</div>
    <div class="card-value" style="font-size:17px">{{ research.regime.regime if research.regime else '—' }}</div>
    <div class="sub">{% if research.regime and research.regime.blocked %}<span class="red">⛔ REGIME BLOCKED</span>{% elif research.regime %}exp ${{ "%.2f"|format(research.regime.expectancy) }} · n={{ research.regime.sample }}{% endif %}</div>
  </div>
  <div class="card">
    <div class="card-label">Latency p95</div>
    <div class="card-value" style="font-size:17px">{{ research.latency.p95 if research.latency and research.latency.p95 is not none else '—' }}<span style="font-size:11px;color:#555"> ms</span></div>
    <div class="sub">{% if research.latency and research.latency.degraded %}<span class="red">degraded → exits x{{ research.latency.exit_mult }}</span>{% else %}p50 {{ research.latency.p50 if research.latency and research.latency.p50 is not none else '—' }}ms{% endif %}</div>
  </div>
  <div class="card">
    <div class="card-label">Avg Slippage</div>
    <div class="card-value" style="font-size:17px">{% if research.slippage and research.slippage.mean_actual_bps is not none %}{{ research.slippage.mean_actual_bps }}<span style="font-size:11px;color:#555"> bps</span>{% else %}—{% endif %}</div>
    <div class="sub">{{ research.slippage.n if research.slippage else 0 }} fills measured</div>
  </div>
  <div class="card">
    <div class="card-label">ML Veto Filter</div>
    <div class="card-value" style="font-size:17px">{% if research.ml and research.ml.enabled %}ON{% else %}<span style="color:#555">off</span>{% endif %}</div>
    <div class="sub">{% if research.ml and research.ml.calibration and research.ml.calibration.n %}cal pred {{ research.ml.calibration.mean_predicted }} / act {{ research.ml.calibration.mean_actual }}{% else %}inert (needs trades){% endif %}</div>
  </div>
</div>

{% if research.cooldowns %}
<section>
  <h2>Per-Ticker Heat &amp; Cooldown</h2>
  <table>
    <thead><tr><th>Ticker</th><th>Heat</th><th>Cooldown</th><th>Consec Losses</th></tr></thead>
    <tbody>
    {% for t, c in research.cooldowns.items() %}
      <tr>
        <td class="sym">{{ t }}</td>
        <td>{{ "%.1f"|format(c.heat) }}</td>
        <td>{% if c.cooldown_seconds > 0 %}<span class="red">{{ c.cooldown_minutes }}m</span>{% else %}<span style="color:#333">—</span>{% endif %}</td>
        <td>{{ c.consec_losses }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</section>
{% endif %}

{% if research.shadow and research.shadow.comparison %}
<section>
  <h2>Champion vs Challengers (hypothetical, paper-logged)</h2>
  <table>
    <thead><tr><th>Strategy</th><th>Trades</th><th>Total P&amp;L</th><th>Win %</th><th>Sharpe</th></tr></thead>
    <tbody>
    {% for name, s in research.shadow.comparison.items() %}
      <tr>
        <td class="sym">{{ 'CHAMPION' if name == 'champion' else name }}</td>
        <td>{{ s.n }}</td>
        <td class="{{ 'green' if s.total >= 0 else 'red' }}">${{ "%.2f"|format(s.total) }}</td>
        <td>{{ "%.0f"|format(s.win_rate * 100) }}%</td>
        <td>{{ s.sharpe if s.sharpe is not none else '—' }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% for r in research.shadow.recommendations %}<div style="color:#fbbf24;font-size:12px;margin-top:7px">⭐ {{ r }}</div>{% endfor %}
</section>
{% endif %}

{% if research.correlation and research.correlation.long %}
<section>
  <h2>30-Day Correlation Heatmap</h2>
  <table style="font-size:11px">
    <thead><tr><th></th>{% for b in research.correlation.tickers %}<th style="text-align:center">{{ b }}</th>{% endfor %}</tr></thead>
    <tbody>
    {% for a in research.correlation.tickers %}
      <tr>
        <td class="sym" style="font-size:11px">{{ a }}</td>
        {% for b in research.correlation.tickers %}
          {% set v = research.correlation.long.get(a, {}).get(b) %}
          <td style="text-align:center;color:#ccc;background:
            {%- if v is none %}#111{% elif a == b %}#262626{% elif v > 0.7 %}#4a1515{% elif v > 0.4 %}#3a2a15{% elif v >= 0 %}#181818{% else %}#15203a{% endif %}">
            {{ "%.2f"|format(v) if v is not none else '·' }}</td>
        {% endfor %}
      </tr>
    {% endfor %}
    </tbody>
  </table>
  <div class="sub" style="margin-top:6px">red = high correlation (blocked if &gt;0.70 with an open position)</div>
</section>
{% endif %}
{% endif %}

</body>
</html>"""


@app.route("/")
def dashboard():
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetOrdersRequest

        client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=PAPER_TRADING)
        account = client.get_account()
        positions = client.get_all_positions()
        try:
            orders = client.get_orders(GetOrdersRequest(status="closed", limit=20))
        except Exception:
            try:
                orders = client.get_orders(GetOrdersRequest(status="all", limit=20))
            except Exception:
                orders = []

        pnl = float(account.equity) - float(account.last_equity)

        return render_template_string(
            TEMPLATE,
            portfolio=f"{float(account.portfolio_value):,.2f}",
            buying_power=f"{float(account.buying_power):,.2f}",
            pnl=pnl,
            pnl_str=f"{abs(pnl):,.2f}",
            positions=positions,
            orders=orders,
            paper=PAPER_TRADING,
            now=datetime.now(ET).strftime("%b %d, %Y  %I:%M %p ET"),
            et=ET,
            research=dashboard_state.snapshot(),
        )
    except Exception as exc:
        logger.error(f"Dashboard error: {exc}")
        return f"<pre style='color:#f87171;background:#111;padding:20px'>Error: {exc}</pre>", 500


def start(port: int = 5001):
    """Start the Flask dashboard in a background daemon thread."""
    thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True),
        daemon=True,
        name="web-dashboard",
    )
    thread.start()
    logger.info(f"Web dashboard running at http://0.0.0.0:{port}")
