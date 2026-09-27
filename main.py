#!/usr/bin/env python3
"""Ichimoku Nifty 200 system: backtest, daily scan and paper trading.

  python main.py backtest            full backtest 2015 -> today, writes results/ + HTML report
  python main.py scan                today's Nifty 200 Ichimoku scan (buy signals + health table)
  python main.py paper init          open a paper account (Rs 5,00,000 by default)
  python main.py paper run           process new daily bars: exits, then entries at close
  python main.py paper status        print positions / equity without downloading
  python main.py paper report        HTML report of the paper account

Add --demo to any command to use synthetic prices (offline smoke test; numbers are meaningless).
"""
import argparse
import os
import sys

import pandas as pd

from ichimoku import config as C
from ichimoku.data import load_data, load_nifty200_symbols, synthetic_data


def get_data(args, symbols=None):
    if args.demo:
        return synthetic_data()
    if args.offline:
        symbols = symbols or load_nifty200_symbols(refresh=False)
    symbols = symbols or load_nifty200_symbols()
    if args.limit:
        symbols = symbols[: args.limit]
    data = load_data(symbols, refresh=not args.offline)
    if not data:
        raise SystemExit("No price data. Check your internet connection (Yahoo Finance) or drop --offline.")
    return data


def get_benchmark(args, eq):
    if args.demo or args.offline:
        return None
    try:
        from ichimoku.data import _download_one
        b, _ = _download_one("^NSEI", C.DATA_START, C.END)
        s = b["Close"].reindex(eq.index).ffill()
        s = s / s.dropna().iloc[0] * C.CFG["capital"]
        s.name = "Nifty 50 (buy & hold)"
        return s
    except Exception as e:
        print("Benchmark download failed:", e)
        return None


def cmd_backtest(args):
    from ichimoku.engine import metrics, simulate
    from ichimoku.indicators import build_panel
    from ichimoku.report import build_report, save_png_summary
    from ichimoku.scanner import scan

    data = get_data(args)
    print(f"{len(data)} symbols loaded")
    P = build_panel(data, C.CFG, C.STRATEGY)
    print("Panel:", P["Close"].shape, "|", P["dates"][0].date(), "->", P["dates"][-1].date())
    trades, eq, n_open, ledger, _ = simulate(P, C.CFG, C.TRAIL, args.start or C.TRADE_START, C.ENTRY_AT, C.RANK_BY)
    m = metrics(eq, trades, n_open, C.CFG)
    print(pd.Series(m).to_frame("value").to_string())

    out = os.path.join(C.RESULTS_DIR, "demo" if args.demo else "backtest")
    os.makedirs(out, exist_ok=True)
    trades.to_csv(os.path.join(out, "trades.csv"), index=False)
    ledger.to_csv(os.path.join(out, "executions.csv"), index=False)
    eq.to_frame("equity").assign(open_positions=n_open).to_csv(os.path.join(out, "equity_curve.csv"))
    pd.Series(m).to_frame("value").to_csv(os.path.join(out, "metrics.csv"))
    save_png_summary(os.path.join(out, "equity_summary.png"), eq, trades, C.CFG, "Ichimoku Nifty 200 backtest")
    sc = scan(data, C.CFG, C.STRATEGY)
    sc.to_csv(os.path.join(out, "scan_latest.csv"), index=False, float_format="%.2f")
    path = build_report(os.path.join(out, "report.html"),
                        title="Ichimoku Nifty 200 — Backtest" + (" (DEMO DATA)" if args.demo else ""),
                        subtitle=f"{len(P['syms'])} stocks · entries at close · ranked by 26-day ROC · Rs {C.CFG['capital']:,.0f} starting capital",
                        eq=eq, trades=trades, n_open=n_open, data=data, cfg=C.CFG, strategy=C.STRATEGY, trail=C.TRAIL,
                        scan_df=sc, benchmark=get_benchmark(args, eq), demo=args.demo)
    print(f"\nWrote {out}/: trades.csv executions.csv equity_curve.csv metrics.csv equity_summary.png scan_latest.csv")
    print("Open the report:", path)


def cmd_scan(args):
    from ichimoku.scanner import scan
    data = get_data(args)
    sc = scan(data, C.CFG, C.STRATEGY)
    os.makedirs(C.RESULTS_DIR, exist_ok=True)
    path = os.path.join(C.RESULTS_DIR, "scan_latest.csv")
    sc.to_csv(path, index=False, float_format="%.2f")
    sig = sc[sc.signal]
    print(f"Scan date {sc.date.max()} | {len(sc)} stocks | {len(sig)} buy signal(s)\n")
    cols = ["symbol", "close", "score", "vs_cloud", "kijun", "dist_kijun_pct", "roc26_pct", "stop_loss", "target_t1"]
    fmt = lambda x: f"{x:,.2f}"
    if len(sig):
        print("BUY SIGNALS (ranked by ROC, the order the paper trader fills free slots):")
        print(sig[cols].to_string(index=False, float_format=fmt))
    watch = sc[~sc.signal & sc.near_cross]
    if len(watch):
        print("\nWATCHLIST (above cloud, within 3% below Kijun):")
        print(watch[cols].head(15).to_string(index=False, float_format=fmt))
    print("\nStrongest trends (score 5, by ROC):")
    print(sc[sc.score == 5][cols].head(15).to_string(index=False, float_format=fmt))
    print("\nSaved", path)


def cmd_paper(args):
    from ichimoku import paper
    if args.demo:
        C.PAPER_DIR = os.path.join(C.ROOT, "paper_demo")
        paper.STATE_FILE = os.path.join(C.PAPER_DIR, "state.json")
    if args.action == "init":
        paper.init(start_date=args.start, capital=args.capital, force=args.force)
    elif args.action == "run":
        paper.run(data=get_data(args, None if args.demo else _paper_universe(paper)))
    elif args.action in ("status", "report"):
        from ichimoku.engine import Engine
        from ichimoku.indicators import build_panel
        from ichimoku.scanner import scan
        e, st = Engine.load(paper.STATE_FILE)
        if args.demo:
            data = synthetic_data()
        else:
            data = load_data(list(dict.fromkeys(load_nifty200_symbols() + list(e.pos))), refresh=False)
        P = build_panel(data, e.cfg, tuple(st["strategy"]))
        e.bind(P)
        if not e.equity:
            print("Paper account has not processed any bars yet. Run `python main.py paper run`.")
            return
        if args.action == "status":
            first = min(e.equity)
            paper.print_summary(e, first, max(e.equity), pd.DataFrame())
            return
        from ichimoku.report import build_report
        trades, eq, n_open, ledger = e.results()
        path = build_report(os.path.join(C.PAPER_DIR, "report.html"),
                            title="Ichimoku Nifty 200 — Paper Trading" + (" (DEMO DATA)" if args.demo else ""),
                            subtitle=f"Forward test since {st['start_date']} · same rules as the backtest · last bar {eq.index[-1].date()}",
                            eq=eq, trades=trades, n_open=n_open, data=data, cfg=e.cfg, strategy=tuple(st["strategy"]), trail=e.trail,
                            positions=e.positions_frame(), scan_df=scan(data, e.cfg, tuple(st["strategy"])),
                            recent_exec=ledger.tail(30).iloc[::-1] if len(ledger) else None, demo=args.demo)
        print("Open the report:", path)


def _paper_universe(paper):
    """Nifty 200 + anything the paper account still holds (a stock can leave the index while held)."""
    if not os.path.exists(paper.STATE_FILE):
        return None
    import json
    held = list(json.load(open(paper.STATE_FILE))["positions"])
    return list(dict.fromkeys(load_nifty200_symbols() + held))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="synthetic prices, no internet needed (numbers are meaningless)")
    ap.add_argument("--offline", action="store_true", help="use cached prices only, do not download")
    ap.add_argument("--limit", type=int, help="only the first N symbols (quick test)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest"); b.add_argument("--start", help="first trading date (default 2015-01-01)")
    sub.add_parser("scan")
    p = sub.add_parser("paper")
    p.add_argument("action", choices=["init", "run", "status", "report"])
    p.add_argument("--start", help="init: first bar to paper trade (default today)")
    p.add_argument("--capital", type=float, help="init: starting capital (default 500000)")
    p.add_argument("--force", action="store_true", help="init: overwrite an existing account")
    args = ap.parse_args(argv)
    {"backtest": cmd_backtest, "scan": cmd_scan, "paper": cmd_paper}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
