"""Ichimoku indicators, entry signals and the aligned price panel (logic unchanged from the notebook)."""
import pandas as pd

xover = lambda a, b: (a > b) & (a.shift(1) <= b.shift(1))


def add_indicators(df, c):
    hi, lo, cl = df["High"], df["Low"], df["Close"]
    donch = lambda n: (lo.rolling(n).min() + hi.rolling(n).max()) / 2
    df = df.copy()
    df["tenkan"], df["kijun"] = donch(c["tenkan"]), donch(c["kijun"])
    df["span_a"] = (df["tenkan"] + df["kijun"]) / 2
    df["span_b"] = donch(c["senkou_b"])
    sh = c["displacement"] - 1
    # adj_a / adj_b: the cloud that sits under TODAY's candle (spans projected forward)
    df["adj_a"], df["adj_b"] = df["span_a"].shift(sh), df["span_b"].shift(sh)
    tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["roc"] = cl.pct_change(26)
    return df


def entry_signal(df, strategy, variant):
    c = df["Close"]
    over, under = c > df["adj_a"], c < df["adj_b"]
    inside = (c > df["adj_a"]) & (c < df["adj_b"])
    zone = {"strong": over, "neutro": inside, "weak": under}
    if strategy == "tenkan_kijun":
        s = xover(df["tenkan"], df["kijun"]) & zone[variant]
    elif strategy == "price_kijun":
        s = xover(c, df["kijun"]) & zone[variant]
    elif strategy == "kumo_breakout":
        s = xover(c, df["adj_a"]) & (c > df["adj_a"]) & (c > df["adj_b"])
    elif strategy == "kumo_twist":
        s = xover(df["adj_a"], df["adj_b"]) & zone[variant]
    else:
        raise ValueError(strategy)
    return s.fillna(False)


def build_panel(data, cfg, strategy):
    dates = pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
    fields = {k: {} for k in ["Open", "High", "Low", "Close", "kijun", "atr", "roc"]}
    sig = {}
    for sym, df in data.items():
        if len(df) < cfg["senkou_b"] + cfg["displacement"] + 5:
            continue
        d = add_indicators(df, cfg)
        for k in fields:
            fields[k][sym] = d[k]
        sig[sym] = entry_signal(d, *strategy)
    syms = list(fields["Close"].keys())
    arr = lambda dct: pd.DataFrame(dct).reindex(index=dates, columns=syms).values.astype(float)
    P = {k: arr(v) for k, v in fields.items()}
    P["Cff"] = pd.DataFrame(P["Close"]).ffill().values
    P["dates"], P["syms"] = dates, syms
    P["sig"] = pd.DataFrame(sig).reindex(index=dates, columns=syms).fillna(False).values.astype(bool)
    return P
