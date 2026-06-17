"""
web_dashboard.py
Local web dashboard for the trading bot.
Run with: python3 web_dashboard.py
Then open: http://localhost:5001
"""

import os
from datetime import datetime

import pandas as pd
import pytz
from flask import Flask, jsonify, render_template

app = Flask(__name__)
ET = pytz.timezone("America/New_York")
TRADES_FILE = os.path.join(os.path.dirname(__file__), "logs", "trades.csv")
STARTING_BALANCE = 100_000.0


def load_sells():
    if not os.path.exists(TRADES_FILE):
        return pd.DataFrame()
    df = pd.read_csv(TRADES_FILE)
    if df.empty:
        return pd.DataFrame()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce").fillna(0)
    return df[df["action"] == "SELL"].copy()


def get_portfolio_value(fallback_pnl: float) -> float:
    try:
        from dotenv import load_dotenv
        load_dotenv()
        from alpaca.trading.client import TradingClient
        client = TradingClient(
            os.environ["ALPACA_API_KEY"],
            os.environ["ALPACA_SECRET_KEY"],
            paper=True,
        )
        return float(client.get_account().portfolio_value)
    except Exception:
        return STARTING_BALANCE + fallback_pnl


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/stats")
def api_stats():
    sells = load_sells()

    total_pnl = float(sells["pnl"].sum()) if not sells.empty else 0.0
    portfolio_value = get_portfolio_value(total_pnl)

    today = datetime.now(ET).strftime("%Y-%m-%d")
    today_sells = sells[sells["timestamp"].dt.strftime("%Y-%m-%d") == today] if not sells.empty else pd.DataFrame()
    today_pnl = float(today_sells["pnl"].sum()) if not today_sells.empty else 0.0

    wins    = int((sells["pnl"] > 0).sum()) if not sells.empty else 0
    losses  = int((sells["pnl"] <= 0).sum()) if not sells.empty else 0
    total   = wins + losses
    win_rate = round(wins / total * 100, 1) if total > 0 else 0.0

    # Equity curve
    if not sells.empty:
        s = sells.sort_values("timestamp")
        s["cum_pnl"] = s["pnl"].cumsum()
        equity = [{"date": s.iloc[0]["timestamp"].strftime("%Y-%m-%d %H:%M"), "value": STARTING_BALANCE}]
        equity += [
            {"date": r["timestamp"].strftime("%Y-%m-%d %H:%M"), "value": round(STARTING_BALANCE + r["cum_pnl"], 2)}
            for _, r in s.iterrows()
        ]
    else:
        equity = [{"date": datetime.now(ET).strftime("%Y-%m-%d %H:%M"), "value": STARTING_BALANCE}]

    strategy_pnl = sells.groupby("strategy")["pnl"].sum().round(2).to_dict() if not sells.empty else {}
    ticker_pnl   = sells.groupby("ticker")["pnl"].sum().round(2).to_dict()   if not sells.empty else {}

    now_et = datetime.now(ET)
    market_open = now_et.weekday() < 5 and (9 * 60 + 30 <= now_et.hour * 60 + now_et.minute < 16 * 60)

    return jsonify({
        "portfolio_value": round(portfolio_value, 2),
        "today_pnl":       round(today_pnl, 2),
        "total_pnl":       round(total_pnl, 2),
        "win_rate":        win_rate,
        "wins":            wins,
        "losses":          losses,
        "total_trades":    total,
        "equity_curve":    equity,
        "strategy_pnl":    {k: float(v) for k, v in strategy_pnl.items()},
        "ticker_pnl":      {k: float(v) for k, v in ticker_pnl.items()},
        "market_open":     market_open,
    })


@app.route("/api/trades")
def api_trades():
    sells = load_sells()
    if sells.empty:
        return jsonify([])
    sells = sells.sort_values("timestamp", ascending=False)
    sells["timestamp"] = sells["timestamp"].dt.strftime("%Y-%m-%d %H:%M")
    return jsonify(sells.fillna("").to_dict(orient="records"))


if __name__ == "__main__":
    print("\n  Dashboard running → http://localhost:5001\n")
    app.run(debug=False, port=5001, host="127.0.0.1")
