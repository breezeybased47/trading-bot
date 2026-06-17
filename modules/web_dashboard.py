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
</body>
</html>"""


@app.route("/")
def dashboard():
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import OrderStatus

        client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=PAPER_TRADING)
        account = client.get_account()
        positions = client.get_all_positions()
        orders = client.get_orders(GetOrdersRequest(status=OrderStatus.FILLED, limit=20))

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
