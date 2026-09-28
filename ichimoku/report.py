"""Self-contained interactive HTML report (Plotly.js from CDN) + a static PNG summary."""
import datetime
import json
import os

import numpy as np
import pandas as pd

from .engine import metrics
from .indicators import add_indicators

TEMPLATE = os.path.join(os.path.dirname(__file__), "report_template.html")


def _r(x, n=2):
    if x is pd.NaT:
        return None
    if isinstance(x, datetime.date) and not isinstance(x, pd.Timestamp):
        return x.isoformat()
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return None
    if isinstance(x, (pd.Timestamp,)):
        return None if pd.isna(x) else x.strftime("%Y-%m-%d")
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else round(float(x), n)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def _records(df, n=2):
    return [{k: _r(v, n) for k, v in row.items()} for row in df.to_dict("records")]


def _trade_windows(trades, data, cfg, pre=70, post=25):
    """OHLC + Ichimoku lines around each trade, keyed by trade id."""
    ind = {}
    out = {}
    for k, t in trades.iterrows():
        sym = t.symbol
        if sym not in data:
            continue
        if sym not in ind:
            ind[sym] = add_indicators(data[sym], cfg)
        d = ind[sym]
        idx = d.index
        a = max(0, idx.searchsorted(t.entry_date) - pre)
        b = min(len(idx), idx.searchsorted(t.exit_date) + post + 1)
        w = d.iloc[a:b]
        out[int(k)] = dict(
            x=[x.strftime("%Y-%m-%d") for x in w.index],
            **{f: [_r(v) for v in w[c].values] for f, c in
               [("o", "Open"), ("h", "High"), ("l", "Low"), ("c", "Close"), ("tk", "tenkan"), ("kj", "kijun"),
                ("sa", "adj_a"), ("sb", "adj_b")]})
    return out


def _monthly(eq):
    m = eq.resample("ME").last()
    first = eq.iloc[0]
    ret = m.pct_change()
    ret.iloc[0] = m.iloc[0] / first - 1
    df = pd.DataFrame({"y": ret.index.year, "m": ret.index.month, "r": ret.values * 100})
    return df.pivot(index="y", columns="m", values="r")


def _yearly(eq, trades):
    rows = []
    for y, s in eq.groupby(eq.index.year):
        prev = eq[eq.index < s.index[0]]
        start = prev.iloc[-1] if len(prev) else s.iloc[0]
        dd = (s / s.cummax() - 1).min() * 100
        tr = trades[(trades.reason != "OPEN") & (trades.exit_date.dt.year == y)]
        rows.append(dict(year=int(y), start=start, end=s.iloc[-1], pnl=s.iloc[-1] - start,
                         ret_pct=(s.iloc[-1] / start - 1) * 100, max_dd_pct=dd, trades_closed=len(tr),
                         win_rate_pct=(tr.pnl > 0).mean() * 100 if len(tr) else None))
    return pd.DataFrame(rows)


def _inline_plotly(html):
    """Embed plotly.js from the installed plotly package so the report works offline; else keep the CDN tag."""
    try:
        import plotly
        js = os.path.join(os.path.dirname(plotly.__file__), "package_data", "plotly.min.js")
        code = open(js, encoding="utf-8").read()
    except Exception:
        return html
    tag = '<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>'
    return html.replace(tag, "<script>" + code.replace("</script", "<\\/script") + "</script>")


def build_report(path, *, title, subtitle, eq, trades, n_open, data, cfg, strategy, trail,
                 benchmark=None, demo=False):
    trades = trades.copy().reset_index(drop=True)
    for c in ("entry_date", "exit_date", "t1_date"):
        trades[c] = pd.to_datetime(trades[c])
    m = metrics(eq, trades, n_open, cfg) if len(eq) > 1 else {}
    closed = trades[trades.reason != "OPEN"]
    closed_list = closed.reset_index(drop=True)
    by_reason = (closed.groupby("reason").agg(trades=("pnl", "size"), net_pnl=("pnl", "sum"), avg_ret_pct=("ret_pct", "mean"),
                                              win_rate_pct=("pnl", lambda s: (s > 0).mean() * 100), avg_days=("days", "mean"))
                 .reset_index()) if len(closed) else pd.DataFrame()
    peak = eq.cummax()
    comm = cfg["commission_pct"] / 100
    tcols = ["symbol", "entry_date", "entry_px", "qty", "invested", "sl_px", "tp_px", "t1_date", "t1_px", "t1_qty",
             "exit_date", "exit_px", "reason", "pnl", "ret_pct", "peak_gain_pct", "days"]
    payload = dict(
        title=title, subtitle=subtitle, demo=demo,
        config=dict(strategy=" / ".join(strategy), capital=cfg["capital"], max_positions=cfg["max_positions"],
                    alloc_pct=cfg["alloc_pct"], sl_pct=cfg["sl_pct"], tp_pct=cfg["tp_pct"],
                    book_pct=cfg["book_fraction"] * 100, trail=f"{trail['value']}% from peak" + (" (never below entry)" if trail.get("be") else "")
                    if trail["kind"] == "pct" else str(trail), commission_pct=cfg["commission_pct"]),
        metrics={k: _r(v) for k, v in m.items()},
        extra=dict(peak_equity=_r(peak.max()) if len(eq) else None,
                   realized_pnl=_r(closed.pnl.sum()), unrealized_pnl=_r(trades[trades.reason == "OPEN"].pnl.sum()),
                   commission=_r((trades.invested * comm / (1 + comm)).sum() +
                                 ((trades.qty - trades.t1_qty.fillna(0)) * trades.exit_px * comm).sum() +
                                 (trades.t1_qty.fillna(0) * trades.t1_px.fillna(0) * comm).sum()) if len(trades) else 0,
                   start=eq.index[0].strftime("%Y-%m-%d") if len(eq) else None,
                   end=eq.index[-1].strftime("%Y-%m-%d") if len(eq) else None),
        equity=dict(x=[d.strftime("%Y-%m-%d") for d in eq.index], y=[_r(v, 0) for v in eq.values],
                    dd=[_r(v) for v in ((eq / peak - 1) * 100).values], pos=[int(v) for v in n_open.reindex(eq.index).fillna(0).values]),
        benchmark=None if benchmark is None else dict(name=benchmark.name, y=[_r(v, 0) for v in benchmark.reindex(eq.index).ffill().values]),
        yearly=_records(_yearly(eq, trades)) if len(eq) else [],
        monthly=(lambda mm: dict(years=[int(y) for y in mm.index], z=[[_r(v) for v in row] for row in mm.reindex(columns=range(1, 13)).values]))(_monthly(eq)) if len(eq) > 20 else None,
        by_reason=_records(by_reason),
        # only finished trades are listed/charted: still-open positions (current holdings) never appear
        trades=_records(closed_list[tcols]),
        windows=_trade_windows(closed_list, data, cfg),
        open_hidden=int((trades.reason == "OPEN").sum()),
    )
    html = open(TEMPLATE).read().replace("/*__DATA__*/null", json.dumps(payload, separators=(",", ":")).replace("</", "<\\/"))
    html = html.replace("__TITLE__", title)
    html = _inline_plotly(html)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(html)
    return path


def save_png_summary(path, eq, trades, cfg, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(3, 1, figsize=(12, 10), gridspec_kw={"height_ratios": [3, 1.2, 1.6]})
    ax[0].plot(eq.index, eq.values, color="#2a78d6", lw=1.8)
    ax[0].axhline(cfg["capital"], color="#898781", lw=1, ls="--")
    ax[0].set_yscale("log"); ax[0].set_title(f"{title} - equity (Rs, log scale)", loc="left")
    ax[0].grid(alpha=.25)
    dd = (eq / eq.cummax() - 1) * 100
    ax[1].fill_between(dd.index, dd.values, 0, color="#e34948", alpha=.35); ax[1].plot(dd.index, dd.values, color="#e34948", lw=1)
    ax[1].set_title("Drawdown %", loc="left"); ax[1].grid(alpha=.25)
    y = _yearly(eq, trades)
    ax[2].bar(y.year.astype(str), y.ret_pct, color=["#2a78d6" if v >= 0 else "#e34948" for v in y.ret_pct])
    for xi, v in enumerate(y.ret_pct):
        ax[2].text(xi, v, f"{v:.0f}%", ha="center", va="bottom" if v >= 0 else "top", fontsize=8)
    ax[2].set_title("Return by year %", loc="left"); ax[2].grid(alpha=.25, axis="y")
    for a in ax:
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    plt.tight_layout(); plt.savefig(path, dpi=110); plt.close(fig)
    return path
