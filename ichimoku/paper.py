"""Forward paper trading with the exact backtest rules.

State lives in paper/state.json. Each `run` downloads the latest daily bars,
replays every bar since the last run through the same Engine used by the
backtest (exits first, then new entries at the close), and saves state again.
Run it once per trading day after 15:30 IST (the GitHub Action does this).
"""
import os

import numpy as np
import pandas as pd

from . import config as C
from .data import load_data, load_nifty200_symbols
from .engine import Engine, metrics
from .indicators import build_panel
from .scanner import scan

STATE_FILE = os.path.join(C.PAPER_DIR, "state.json")


def init(start_date=None, capital=None, force=False):
    os.makedirs(C.PAPER_DIR, exist_ok=True)
    if os.path.exists(STATE_FILE) and not force:
        raise SystemExit(f"{STATE_FILE} exists. Use --force to start over (this deletes the paper history).")
    cfg = dict(C.CFG)
    if capital:
        cfg["capital"] = capital
    e = Engine(cfg, C.TRAIL, C.ENTRY_AT, C.RANK_BY)
    start = start_date or pd.Timestamp.today().strftime("%Y-%m-%d")
    e.save(STATE_FILE, extra=dict(start_date=start, strategy=list(C.STRATEGY)))
    print(f"Paper account created: capital Rs {cfg['capital']:,.0f}, trading from {start}")
    print("Run `python main.py paper run` after each market close.")


def run(data=None, verbose=True):
    if not os.path.exists(STATE_FILE):
        raise SystemExit("No paper account. Run `python main.py paper init` first.")
    e, st = Engine.load(STATE_FILE)
    strategy = tuple(st.get("strategy", C.STRATEGY))
    if data is None:
        universe = list(dict.fromkeys(load_nifty200_symbols() + list(e.pos)))
        data = load_data(universe)
    P = build_panel(data, e.cfg, strategy)
    e.bind(P)
    dates = P["dates"]

    end_i = len(dates)
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    if dates[-1].date() == now.date() and (now.hour, now.minute) < (15, 45):
        end_i -= 1          # today's bar is still forming during market hours: never trade on it
        if verbose:
            print(f"Market still open ({now:%H:%M} IST): skipping today's incomplete bar.")

    if e.last_date is None:
        start_i = int(np.searchsorted(dates.values, np.datetime64(st["start_date"])))
    else:
        start_i = e.di[e.last_date] + 1
    if start_i >= end_i:
        if verbose:
            print(f"Up to date: last processed bar {dates[-1].date()} (no new market data yet).")
        write_outputs(e, P, data, strategy, new_exec=pd.DataFrame())
        return e

    n_led = len(e.ledger)
    e.run(start_i, end_i)
    new_exec = pd.DataFrame(e.ledger[n_led:])
    e.save(STATE_FILE, extra=dict(start_date=st["start_date"], strategy=list(strategy)))
    write_outputs(e, P, data, strategy, new_exec)
    if verbose:
        print_summary(e, dates[start_i], dates[end_i - 1], new_exec)
    return e


def write_outputs(e, P, data, strategy, new_exec):
    trades, eq, n_open, ledger = e.results()
    d = C.PAPER_DIR
    e.positions_frame().to_csv(os.path.join(d, "positions.csv"), index=False, float_format="%.2f")
    trades.to_csv(os.path.join(d, "trades.csv"), index=False)
    ledger.to_csv(os.path.join(d, "executions.csv"), index=False)
    eq.to_frame("equity").assign(open_positions=n_open).to_csv(os.path.join(d, "equity.csv"))
    sc = scan(data, e.cfg, strategy)
    sc.to_csv(os.path.join(d, "scan_latest.csv"), index=False, float_format="%.2f")
    if len(new_exec):
        with open(os.path.join(d, "journal.md"), "a") as f:
            for r in new_exec.itertuples():
                f.write(f"- {pd.Timestamp(r.date).date()} | {r.action:<16} | {r.symbol:<12} | qty {int(r.qty):>6} | @ {r.fill_px:,.2f}\n")


def print_summary(e, first, last, new_exec):
    trades, eq, n_open, _ = e.results()
    cap = e.cfg["capital"]
    print("=" * 78)
    print(f"PAPER TRADING  |  bars processed {first.date()} -> {last.date()}")
    print("=" * 78)
    if len(new_exec):
        print("\nExecutions in this run:")
        print(new_exec[["date", "action", "symbol", "qty", "fill_px"]].to_string(index=False))
    else:
        print("\nNo executions in this run.")
    pos = e.positions_frame()
    print(f"\nOpen positions ({len(pos)}/{e.cfg['max_positions']}):")
    if len(pos):
        cols = ["symbol", "entry_date", "entry_px", "qty_open", "last_close", "ret_pct", "stop_loss", "target_t1", "stage"]
        print(pos[cols].to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    print(f"\nCash          Rs {e.cash:,.0f}")
    print(f"Equity        Rs {eq.iloc[-1]:,.0f}   ({(eq.iloc[-1] / cap - 1) * 100:+.2f}% since start)")
    if len(eq) > 1:
        m = metrics(eq, trades, n_open, e.cfg)
        print(f"Max drawdown  {m['max_DD_pct']:.2f}%   |   closed trades {len(e.trades)}")
    print("\nFiles updated in paper/: positions.csv trades.csv executions.csv equity.csv scan_latest.csv journal.md")
