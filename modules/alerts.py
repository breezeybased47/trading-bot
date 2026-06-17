"""
alerts.py
Sends Telegram messages on key trading events.
Telegram is optional — if keys are missing, alerts are silently skipped.
"""

import logging

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def _send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": f"🤖 TradingBot\n\n{text}", "parse_mode": "HTML"},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning(f"Telegram failed: {resp.text}")
    except Exception as exc:
        logger.error(f"Telegram error: {exc}")


def trade_opened(ticker: str, side: str, qty: int, price: float, strategy: str):
    _send(
        f"📈 <b>TRADE OPENED</b>\n"
        f"Ticker:   {ticker}\n"
        f"Side:     {side}\n"
        f"Qty:      {qty} shares\n"
        f"Price:    ${price:.2f}\n"
        f"Strategy: {strategy}"
    )


def trade_closed(ticker: str, entry: float, exit_p: float, pnl: float, reason: str):
    emoji = "✅" if pnl >= 0 else "❌"
    _send(
        f"{emoji} <b>TRADE CLOSED</b>\n"
        f"Ticker: {ticker}\n"
        f"Entry:  ${entry:.2f}  →  Exit: ${exit_p:.2f}\n"
        f"P&L:    ${pnl:+.2f}\n"
        f"Reason: {reason}"
    )


def stop_hit(ticker: str, price: float, loss: float):
    _send(
        f"🛑 <b>STOP LOSS TRIGGERED</b>\n"
        f"Ticker:    {ticker}\n"
        f"Exit:      ${price:.2f}\n"
        f"Loss:      ${loss:.2f}"
    )


def daily_limit_hit(portfolio: float, loss_pct: float):
    _send(
        f"🚨 <b>DAILY LOSS LIMIT HIT</b>\n"
        f"Portfolio: ${portfolio:,.2f}\n"
        f"Loss:      {loss_pct:.1%}\n"
        f"All trading halted for today."
    )
