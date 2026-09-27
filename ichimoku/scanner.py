"""Daily Nifty 200 scan: today's entry signals plus an Ichimoku health table for every stock."""
import numpy as np
import pandas as pd

from .indicators import add_indicators, entry_signal


def scan(data, cfg, strategy, asof=None):
    """One row per stock for the latest bar (or `asof`).

    signal      : the strategy's entry trigger fired on this bar (what the paper trader buys)
    score (0-5) : close above cloud, Tenkan > Kijun, close > Kijun, future cloud bullish,
                  Chikou (close) above the close 26 bars ago
    near_cross  : above the cloud and within 3% below Kijun - candidates for a signal soon
    """
    rows = []
    for sym, df in data.items():
        if len(df) < cfg["senkou_b"] + cfg["displacement"] + 5:
            continue
        d = add_indicators(df, cfg)
        d["signal"] = entry_signal(d, *strategy)
        if asof is not None:
            d = d.loc[:pd.Timestamp(asof)]
            if d.empty:
                continue
        r = d.iloc[-1]
        top, bot = max(r.adj_a, r.adj_b), min(r.adj_a, r.adj_b)
        chikou_ok = len(d) > cfg["displacement"] and r.Close > d["Close"].iloc[-1 - cfg["displacement"]]
        checks = [r.Close > top, r.tenkan > r.kijun, r.Close > r.kijun, r.span_a > r.span_b, chikou_ok]
        rows.append(dict(
            symbol=sym, date=d.index[-1].date(), close=r.Close, signal=bool(r.signal),
            score=int(sum(bool(x) for x in checks)),
            vs_cloud="above" if r.Close > top else ("below" if r.Close < bot else "inside"),
            tenkan=r.tenkan, kijun=r.kijun, cloud_top=top, cloud_bottom=bot,
            dist_kijun_pct=(r.Close / r.kijun - 1) * 100 if r.kijun else np.nan,
            roc26_pct=r.roc * 100,
            near_cross=bool(r.Close > r.adj_a and r.Close <= r.kijun and r.Close >= r.kijun * 0.97),
            stop_loss=r.Close * (1 - cfg["sl_pct"] / 100), target_t1=r.Close * (1 + cfg["tp_pct"] / 100),
        ))
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    # signals first, in the order the engine takes them (ROC); then the rest by health score
    sig = out[out.signal].sort_values("roc26_pct", ascending=False)
    rest = out[~out.signal].sort_values(["score", "roc26_pct"], ascending=False)
    return pd.concat([sig, rest]).reset_index(drop=True)
