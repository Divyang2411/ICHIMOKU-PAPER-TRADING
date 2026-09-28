# ===================== ICHIMOKU NIFTY 200 — MY PRIVATE RESULTS =====================
# Backtest + today's scan + paper account (decrypted with your key). Run this one cell.
# Keep this notebook OUTSIDE the repo folder so its outputs are never committed.
import os, sys, json, subprocess, webbrowser, warnings
warnings.filterwarnings("ignore")

REPO = os.path.expanduser("~/ICHIMOKU-PAPER-TRADING")
URL = "https://github.com/Divyang2411/ICHIMOKU-PAPER-TRADING.git"
RUN_BACKTEST = True          # False = only scan + paper account (faster)
OPEN_REPORTS = False         # True = also open the interactive HTML reports in your browser

# 1. latest code + encrypted paper account from GitHub
if not os.path.isdir(os.path.join(REPO, ".git")):
    subprocess.run(["git", "clone", "-q", URL, REPO], check=True)
else:
    subprocess.run(["git", "-C", REPO, "pull", "-q", "--ff-only", "--autostash"])
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", os.path.join(REPO, "requirements.txt")], check=True)
os.chdir(REPO)
sys.path.insert(0, REPO)
for m in [m for m in sys.modules if m == "ichimoku" or m.startswith("ichimoku.")]:
    del sys.modules[m]                                   # always use the code just pulled

import numpy as np, pandas as pd, matplotlib.pyplot as plt
from IPython.display import display, Markdown
from ichimoku import config as C, vault
from ichimoku.data import load_nifty200_symbols, load_data
from ichimoku.indicators import build_panel
from ichimoku.engine import Engine, simulate, metrics
from ichimoku.scanner import scan
from ichimoku.report import build_report, _yearly
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 40)
H = lambda t: display(Markdown(t))

# 2. decrypt the paper account (key: ~/.ichimoku_paper_key or PAPER_KEY)
paper_state, held = None, []
if os.path.exists(vault.ENC_FILE):
    try:
        vault.unlock()
        paper_state = json.load(open(os.path.join(C.PAPER_DIR, "state.json")))
        held = list(paper_state["positions"])
    except SystemExit as e:
        print("Paper account not decrypted:", e)
elif os.path.exists(os.path.join(C.PAPER_DIR, "state.json")):
    paper_state = json.load(open(os.path.join(C.PAPER_DIR, "state.json"))); held = list(paper_state["positions"])

# 3. prices (first run downloads ~12 years for 200 stocks: 5-10 min; later only the last weeks)
symbols = load_nifty200_symbols()
data_all = load_data(list(dict.fromkeys(symbols + held)))
data = {s: d for s, d in data_all.items() if s in symbols}
print(f"{len(data)} Nifty 200 stocks | last bar {max(d.index[-1] for d in data.values()).date()}")
inr = lambda x: f"₹{x:,.0f}"
cols = ["symbol", "entry_date", "entry_px", "qty", "sl_px", "tp_px", "t1_date", "t1_px", "exit_date", "exit_px", "reason", "pnl", "ret_pct", "days"]

# 4. backtest
if RUN_BACKTEST:
    P = build_panel(data, C.CFG, C.STRATEGY)
    trades, eq, n_open, ledger, _ = simulate(P, C.CFG, C.TRAIL, C.TRADE_START, C.ENTRY_AT, C.RANK_BY)
    m = metrics(eq, trades, n_open, C.CFG)
    H(f"## Backtest {eq.index[0].date()} → {eq.index[-1].date()}  ·  start {inr(C.CFG['capital'])}")
    display(pd.Series(m, name="value").to_frame())
    fig, ax = plt.subplots(3, 1, figsize=(13, 10), gridspec_kw={"height_ratios": [3, 1.2, 1.6]})
    ax[0].plot(eq.index, eq.values, color="#2a78d6"); ax[0].set_yscale("log"); ax[0].axhline(C.CFG["capital"], ls="--", c="gray", lw=1)
    ax[0].set_title("Equity (₹, log)", loc="left"); ax[0].grid(alpha=.25)
    dd = (eq / eq.cummax() - 1) * 100
    ax[1].fill_between(dd.index, dd, 0, color="#e34948", alpha=.35); ax[1].set_title("Drawdown %", loc="left"); ax[1].grid(alpha=.25)
    y = _yearly(eq, trades)
    ax[2].bar(y.year.astype(str), y.ret_pct, color=["#2a78d6" if v >= 0 else "#e34948" for v in y.ret_pct])
    for i, v in enumerate(y.ret_pct): ax[2].text(i, v, f"{v:.0f}%", ha="center", va="bottom" if v >= 0 else "top", fontsize=8)
    ax[2].set_title("Return by year %", loc="left"); ax[2].grid(alpha=.25, axis="y")
    plt.tight_layout(); plt.show()
    H("**Year by year**"); display(y.round(2).set_index("year"))
    closed = trades[trades.reason != "OPEN"]
    H("**By exit reason**")
    display(closed.groupby("reason").agg(trades=("pnl", "size"), net_pnl=("pnl", "sum"), avg_ret_pct=("ret_pct", "mean"),
                                         avg_days=("days", "mean")).round(2))
    H("**Last 20 trades** (all trades: `results/backtest/trades.csv`)")
    display(trades.sort_values("entry_date").tail(20)[cols].round(2))
    out = os.path.join(C.RESULTS_DIR, "backtest"); os.makedirs(out, exist_ok=True)
    trades.to_csv(os.path.join(out, "trades.csv"), index=False)
    bt_report = build_report(os.path.join(out, "report.html"), title="Ichimoku Nifty 200 — Backtest",
                             subtitle=f"{len(P['syms'])} stocks · entries at close · ranked by 26-day ROC",
                             eq=eq, trades=trades, n_open=n_open, data=data, cfg=C.CFG, strategy=C.STRATEGY, trail=C.TRAIL)
    print("Interactive backtest report (per-trade charts):", bt_report)
    if OPEN_REPORTS: webbrowser.open("file://" + bt_report)

# 5. today's scan
sc = scan(data, C.CFG, C.STRATEGY)
scols = ["symbol", "close", "score", "vs_cloud", "kijun", "dist_kijun_pct", "roc26_pct", "stop_loss", "target_t1"]
H(f"## Scan — {sc.date.max()}")
sig = sc[sc.signal]
H(f"**Signals ({len(sig)})** — ranked by ROC"); display(sig[scols].round(2) if len(sig) else "none")
H("**Near a signal** (above cloud, within 3% below Kijun)"); display(sc[~sc.signal & sc.near_cross][scols].head(15).round(2))

# 6. paper account
H("## Paper account")
if paper_state is None:
    print("No paper account yet (or not decrypted). See README → 'Private paper account'.")
else:
    e, st = Engine.load(os.path.join(C.PAPER_DIR, "state.json"))
    e.bind(build_panel(data_all, e.cfg, tuple(st["strategy"])))
    if not e.equity:
        print(f"Account opened {st['start_date']} with {inr(e.cfg['capital'])}; no market day processed yet.")
    else:
        ptr, peq, pno, pled = e.results()
        cap = e.cfg["capital"]
        print(f"Since {st['start_date']}: equity {inr(peq.iloc[-1])} ({(peq.iloc[-1] / cap - 1) * 100:+.2f}%) | "
              f"cash {inr(e.cash)} | positions {len(e.pos)}/{e.cfg['max_positions']} | closed trades {len(e.trades)} | last bar {peq.index[-1].date()}")
        fig, ax = plt.subplots(figsize=(13, 3.5)); ax.plot(peq.index, peq.values, color="#2a78d6")
        ax.axhline(cap, ls="--", c="gray", lw=1); ax.set_title("Paper equity (₹)", loc="left"); ax.grid(alpha=.25); plt.show()
        H("**Open positions** (stop_loss = today's stop, trail after T1)")
        pos = e.positions_frame(); display(pos.round(2) if len(pos) else "none")
        H("**Latest executions**"); display(pled.tail(15).iloc[::-1][["date", "action", "symbol", "qty", "fill_px"]].round(2) if len(pled) else "none")
        cl = ptr[ptr.reason != "OPEN"]
        if len(cl):
            H("**Closed trades**"); display(cl[cols].round(2))
        pr = build_report(os.path.join(C.PAPER_DIR, "report.html"), title="Ichimoku Nifty 200 — Paper Trading",
                          subtitle=f"Forward test since {st['start_date']}", eq=peq, trades=ptr, n_open=pno, data=data_all,
                          cfg=e.cfg, strategy=tuple(st["strategy"]), trail=e.trail, positions=pos, recent_exec=pled.tail(30).iloc[::-1])
        print("Interactive paper report:", pr)
        if OPEN_REPORTS: webbrowser.open("file://" + pr)
