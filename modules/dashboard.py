"""
dashboard.py
Terminal UI using the Rich library.
Displays portfolio summary, open positions, and live market scanner.
"""

from datetime import datetime
from typing import Dict

import pytz
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from modules.trade_logger import today_stats
from config import PAPER_TRADING, TICKERS

ET = pytz.timezone("America/New_York")
console = Console()


def render(
    positions:     Dict,
    portfolio:     float,
    cash:          float,
    prices:        Dict[str, float],
    indicators:    Dict[str, dict],
    halted:        bool,
) -> Panel:
    """Build and return the full dashboard panel (called every 2 seconds)."""

    now  = datetime.now(ET).strftime("%Y-%m-%d  %H:%M:%S ET")
    mode = "[bold red]LIVE[/]" if not PAPER_TRADING else "[bold yellow]PAPER TRADING[/]"
    stats = today_stats()

    # ── Portfolio summary ────────────────────────────────────────────────────
    summary = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    summary.add_column("k", style="dim",    min_width=18)
    summary.add_column("v", style="bold",   min_width=16)
    summary.add_row("Portfolio Value",  f"${portfolio:,.2f}")
    summary.add_row("Cash Available",   f"${cash:,.2f}")
    pnl_color = "green" if stats["total_pnl"] >= 0 else "red"
    summary.add_row("Today P&L",        f"[{pnl_color}]${stats['total_pnl']:+.2f}[/]")
    summary.add_row("Win Rate",
                    f"{stats['win_rate']:.0f}%  "
                    f"({stats['wins']}W / {stats['losses']}L / {stats['total_trades']} trades)")
    summary.add_row("Status",  "[bold red]HALTED[/]" if halted else "[bold green]ACTIVE[/]")

    # ── Open positions ───────────────────────────────────────────────────────
    pos_tbl = Table(title="Open Positions", box=box.SIMPLE_HEAD, min_width=52)
    for col in ("Ticker", "Qty", "Entry", "Now", "P&L", "Stop"):
        pos_tbl.add_column(col, justify="right" if col != "Ticker" else "left")

    if positions:
        for ticker, pos in positions.items():
            stop_price = pos.trailing_stop if pos.trailing_active else pos.hard_stop
            pnl_style  = "green" if pos.pnl >= 0 else "red"
            pos_tbl.add_row(
                f"[cyan]{ticker}[/]",
                str(pos.qty),
                f"${pos.entry:.2f}",
                f"${pos.current_price:.2f}",
                f"[{pnl_style}]${pos.pnl:+.2f}[/]",
                f"${stop_price:.2f}",
            )
    else:
        pos_tbl.add_row("[dim]no positions[/]", "", "", "", "", "")

    # ── Market scanner ───────────────────────────────────────────────────────
    scan = Table(title="Market Scanner", box=box.SIMPLE_HEAD, min_width=60)
    scan.add_column("Ticker",  style="cyan", min_width=7)
    scan.add_column("Price",   justify="right")
    scan.add_column("RSI",     justify="right")
    scan.add_column("MACD ▲▼", justify="right")
    scan.add_column("vs VWAP", justify="right")
    scan.add_column("Signal",  justify="center")

    for ticker in TICKERS:
        price = prices.get(ticker)
        ind   = indicators.get(ticker, {})
        rsi   = ind.get("rsi")
        hist  = ind.get("macd_hist")
        vwap  = ind.get("vwap")

        price_s = f"${price:.2f}" if price else "—"

        if rsi is None:
            rsi_s = "—"
        elif rsi < 30:
            rsi_s = f"[bold red]{rsi:.1f}[/]"
        elif rsi > 70:
            rsi_s = f"[bold green]{rsi:.1f}[/]"
        else:
            rsi_s = f"{rsi:.1f}"

        hist_s = f"[green]{hist:+.3f}[/]" if hist and hist > 0 else (
                 f"[red]{hist:+.3f}[/]"   if hist else "—")

        vs_vwap_s = ""
        if price and vwap:
            d = (price - vwap) / vwap * 100
            vs_vwap_s = f"{'▲' if d >= 0 else '▼'}{abs(d):.2f}%"

        # Simple signal badge
        if rsi and hist is not None:
            if rsi < 30:
                badge = "[bold red]OVERSOLD[/]"
            elif rsi > 70:
                badge = "[bold green]OVERBOUGHT[/]"
            elif rsi > 55 and hist > 0:
                badge = "[green]↑ MOM[/]"
            elif rsi < 45 and hist < 0:
                badge = "[red]↓ WEAK[/]"
            else:
                badge = "—"
        else:
            badge = "—"

        scan.add_row(ticker, price_s, rsi_s, hist_s, vs_vwap_s, badge)

    header = Text(f"  {mode}    {now}", style="bold white")
    return Panel(
        Columns([summary, pos_tbl, scan], expand=True),
        title=header,
        border_style="blue",
    )
