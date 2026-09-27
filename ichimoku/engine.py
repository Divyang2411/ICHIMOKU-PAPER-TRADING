"""Portfolio engine shared by the backtest and the paper trader.

This is the notebook's `simulate()` loop, restructured so it can process one
bar at a time and save/restore its state as JSON. The trading rules (signals,
SL / T1 / trail / breakeven, ROC ranking, compounding sizing, commission) are
unchanged; tests/test_engine.py checks trade-for-trade equality with the
notebook's original function.
"""
import json
import math

import numpy as np
import pandas as pd


def _f(x):
    """numpy scalar -> plain python for JSON."""
    if x is pd.NaT:
        return None
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(x).strftime("%Y-%m-%d")
    return x


def _jsonable(d):
    return {k: (_jsonable(v) if isinstance(v, dict) else _f(v)) for k, v in d.items()}


DATE_KEYS = ("entry_date", "exit_date", "t1_date", "date")


class Engine:
    def __init__(self, cfg, trail, entry_at="close", rank_by="roc"):
        self.cfg, self.trail, self.entry_at, self.rank_by = cfg, trail, entry_at, rank_by
        self.cash = float(cfg["capital"])
        self.pos = {}                 # symbol -> position dict
        self.trades, self.ledger = [], []
        self.equity = {}              # date -> equity after that bar
        self.n_open = {}              # date -> open positions after that bar
        self.last_date = None         # last bar processed
        self.slip = cfg["slippage_ticks"] * cfg["tick_size"]
        self.comm = cfg["commission_pct"] / 100
        self.sl_f, self.tp_f = 1 - cfg["sl_pct"] / 100, 1 + cfg["tp_pct"] / 100

    # ------------------------------------------------------------------ panel
    def bind(self, P):
        self.P = P
        self.dates, self.syms = P["dates"], P["syms"]
        self.si = {s: j for j, s in enumerate(self.syms)}
        self.di = {d: i for i, d in enumerate(self.dates)}
        missing = [s for s in self.pos if s not in self.si]
        if missing:
            raise ValueError(f"held symbols missing from price panel: {missing}")
        return self

    def _bar(self, i, j):
        P = self.P
        return dict(open=P["Open"][i, j], high=P["High"][i, j], low=P["Low"][i, j], close=P["Close"][i, j])

    def mark(self, i):
        Cff = self.P["Cff"]
        return self.cash + sum(p["qty"] * Cff[i, self.si[s]] for s, p in self.pos.items())

    # ------------------------------------------------------------------ orders
    def _sell(self, sym, qty, raw_fill, i, reason, rule, level=np.nan):
        p = self.pos[sym]
        j = self.si[sym]
        assert np.isfinite(raw_fill), f"non-finite exit fill {sym} {self.dates[i]}"
        px = raw_fill if self.slip == 0 else raw_fill - self.slip
        self.cash += qty * px * (1 - self.comm)
        p["proceeds"] += qty * px * (1 - self.comm)
        p["qty"] -= qty
        b = self._bar(i, j)
        self.ledger.append(dict(symbol=sym, date=self.dates[i], action="EXIT_" + reason, qty=qty, raw_px=raw_fill, fill_px=px,
                                open=b["open"], high=b["high"], low=b["low"], close=b["close"], rule=rule, level=level))
        if reason == "T1":
            p.update(t1_date=self.dates[i], t1_raw_px=raw_fill, t1_px=px, t1_qty=qty, t1_level=level)
        if p["qty"] == 0:
            self.trades.append(self._trade_record(sym, p, i, px, raw_fill, reason, rule, level, b))
            del self.pos[sym]

    def _trade_record(self, sym, p, i, px, raw_fill, reason, rule, level, b, pnl=None):
        if pnl is None:
            pnl = p["proceeds"] - p["cost"]
        eb = p["eb"]
        return dict(symbol=sym, entry_date=pd.Timestamp(p["entry_date"]), exit_date=self.dates[i],
                    entry_px=p["entry_px"], exit_px=px, entry_raw_px=p["entry_raw_px"], exit_raw_px=raw_fill,
                    qty=p["qty0"], invested=p["cost"], pnl=pnl, ret_pct=pnl / p["cost"] * 100,
                    hit_target=p["t1"], reason=reason, peak_gain_pct=(p["peak"] / p["entry_px"] - 1) * 100,
                    days=i - self.di[pd.Timestamp(p["entry_date"])],
                    entry_open=eb["open"], entry_high=eb["high"], entry_low=eb["low"], entry_close=eb["close"],
                    exit_open=b["open"], exit_high=b["high"], exit_low=b["low"], exit_close=b["close"],
                    fill_rule=rule, exit_level=level, sl_px=p["sl_px"], tp_px=p["tp_px"],
                    t1_date=pd.Timestamp(p["t1_date"]) if p.get("t1_date") is not None else pd.NaT,
                    t1_raw_px=p.get("t1_raw_px", np.nan), t1_px=p.get("t1_px", np.nan), t1_qty=p.get("t1_qty", 0))

    def candidates(self, row, price_row):
        """Signal stocks on `row` that are not held, ranked the way entries are taken."""
        P = self.P
        held = {self.si[s] for s in self.pos}
        cand = [j for j in np.flatnonzero(P["sig"][row] & ~np.isnan(price_row)) if j not in held]
        if self.rank_by == "roc":
            ROC = P["roc"]
            cand.sort(key=lambda j: -(ROC[row, j] if not np.isnan(ROC[row, j]) else -9))
        return cand

    def _try_enter(self, row, price_row, eq, i):
        cfg, comm = self.cfg, self.comm
        free = cfg["max_positions"] - len(self.pos)
        if free <= 0:
            return
        cand = self.candidates(row, price_row)
        for j in cand[:free]:
            raw = price_row[j]
            assert np.isfinite(raw), f"non-finite entry price {self.syms[j]} {self.dates[i]}"
            px = raw if self.slip == 0 else raw + self.slip
            q = min(math.floor(cfg["alloc_pct"] / 100 * eq / (px * (1 + comm))), math.floor(self.cash / (px * (1 + comm))))
            if q < 1:
                continue
            cost = q * px * (1 + comm)
            self.cash -= cost
            b = self._bar(i, j)
            sym = self.syms[j]
            self.pos[sym] = dict(entry_date=self.dates[i], entry_px=px, entry_raw_px=raw, eb=b, qty0=q, qty=q, cost=cost,
                                 proceeds=0.0, t1=False, peak=px, sl_px=px * self.sl_f, tp_px=px * self.tp_f)
            self.ledger.append(dict(symbol=sym, date=self.dates[i], action="ENTRY", qty=q, raw_px=raw, fill_px=px,
                                    open=b["open"], high=b["high"], low=b["low"], close=b["close"],
                                    rule="close" if self.entry_at == "close" else "open", level=np.nan))

    # ------------------------------------------------------------------ bar loop
    def step(self, i):
        P, cfg, trail = self.P, self.cfg, self.trail
        O, H, L, C, KJ, ATR = P["Open"], P["High"], P["Low"], P["Close"], P["kijun"], P["atr"]
        if self.entry_at == "next_open" and self.last_date is not None:
            prev = self.di[self.last_date]
            self._try_enter(prev, O[i], self.equity[self.last_date], i)
        for sym in list(self.pos):
            p, j = self.pos[sym], self.si[sym]
            o, h, l, c = O[i, j], H[i, j], L[i, j], C[i, j]
            if np.isnan(h) or np.isnan(l) or np.isnan(o):
                continue
            if not p["t1"]:
                if l <= p["sl_px"]:
                    self._sell(sym, p["qty"], min(o, p["sl_px"]), i, "SL", "min(open,sl_px)", p["sl_px"])
                elif h >= p["tp_px"]:
                    fill = max(o, p["tp_px"])
                    half = int(round(p["qty0"] * cfg["book_fraction"]))
                    if p["qty0"] < 2:
                        self._sell(sym, p["qty"], fill, i, "TARGET_FULL", "max(open,tp_px)", p["tp_px"])
                        continue
                    half = min(max(half, 1), p["qty0"] - 1)
                    self._sell(sym, half, fill, i, "T1", "max(open,tp_px)", p["tp_px"])
                    p["t1"] = True
                    p["peak"] = max(p["peak"], h)
                else:
                    p["peak"] = max(p["peak"], h)
            else:
                lvl = self.trail_level(p, i, j)
                if l <= lvl:
                    self._sell(sym, p["qty"], min(o, lvl), i, "TRAIL", "min(open,trail_level)", lvl)
                elif trail["kind"] == "kijun" and not np.isnan(KJ[i, j]) and c < KJ[i, j]:
                    self._sell(sym, p["qty"], c, i, "TRAIL", "close", KJ[i, j])
                else:
                    p["peak"] = max(p["peak"], h)
        if self.entry_at == "close":
            self._try_enter(i, C[i], self.mark(i), i)
        d = self.dates[i]
        self.equity[d] = self.mark(i)
        self.n_open[d] = len(self.pos)
        self.last_date = d

    def trail_level(self, p, i, j):
        trail, ATR = self.trail, self.P["atr"]
        lvl = -np.inf
        if trail["kind"] == "pct":
            lvl = p["peak"] * (1 - trail["value"] / 100)
        elif trail["kind"] == "atr" and i > 0 and not np.isnan(ATR[i - 1, j]):
            lvl = p["peak"] - trail["value"] * ATR[i - 1, j]
        lvl = max(lvl, p["sl_px"])
        if trail.get("be", False):
            lvl = max(lvl, p["entry_px"])
        return lvl

    def run(self, start_i, end_i=None):
        end_i = len(self.dates) if end_i is None else end_i
        for i in range(start_i, end_i):
            self.step(i)
        return self

    # ------------------------------------------------------------------ outputs
    def open_trade_records(self):
        """Open positions marked to the last close (same as the notebook's end-of-run block)."""
        T = len(self.dates)
        out = []
        for sym, p in self.pos.items():
            j = self.si[sym]
            last = self.P["Cff"][T - 1, j]
            val = p["qty"] * last * (1 - self.comm) + p["proceeds"]
            out.append(self._trade_record(sym, p, T - 1, last, last, "OPEN", "last_close(MTM)", np.nan,
                                          self._bar(T - 1, j), pnl=val - p["cost"]))
        return out

    def results(self):
        trades = pd.DataFrame(self.trades + self.open_trade_records())
        eq = pd.Series(self.equity, dtype=float)
        eq.index = pd.DatetimeIndex(eq.index)
        n_open = pd.Series(self.n_open, dtype=int)
        n_open.index = pd.DatetimeIndex(n_open.index)
        return trades, eq, n_open, pd.DataFrame(self.ledger)

    def positions_frame(self):
        """Current holdings with live SL / target / trail levels for the next session."""
        if not self.pos:
            return pd.DataFrame()
        T = len(self.dates)
        rows = []
        for sym, p in self.pos.items():
            j = self.si[sym]
            last = self.P["Cff"][T - 1, j]
            stop = self.trail_level(p, T - 1, j) if p["t1"] else p["sl_px"]
            val = p["qty"] * last
            rows.append(dict(symbol=sym, entry_date=pd.Timestamp(p["entry_date"]).date(), entry_px=p["entry_px"],
                             qty_initial=p["qty0"], qty_open=p["qty"], invested=p["cost"], last_close=last,
                             market_value=val,
                             unrealized_pnl=val * (1 - self.comm) + p["proceeds"] - p["cost"],
                             ret_pct=(last / p["entry_px"] - 1) * 100,
                             stage="T1 booked - trailing" if p["t1"] else "Initial",
                             stop_loss=stop, target_t1=None if p["t1"] else p["tp_px"],
                             peak=p["peak"], days_held=T - 1 - self.di[pd.Timestamp(p["entry_date"])]))
        return pd.DataFrame(rows).sort_values("entry_date").reset_index(drop=True)

    # ------------------------------------------------------------------ persistence
    def to_state(self):
        return dict(
            cash=self.cash, last_date=_f(self.last_date) if self.last_date is not None else None,
            positions={s: _jsonable(p) for s, p in self.pos.items()},
            trades=[_jsonable(t) for t in self.trades], ledger=[_jsonable(r) for r in self.ledger],
            equity={_f(d): v for d, v in self.equity.items()},
            n_open={_f(d): int(v) for d, v in self.n_open.items()},
        )

    def save(self, path, extra=None):
        st = self.to_state()
        st["config"] = dict(cfg=self.cfg, trail=self.trail, entry_at=self.entry_at, rank_by=self.rank_by)
        if extra:
            st.update(extra)
        with open(path, "w") as f:
            json.dump(st, f, indent=1)

    @classmethod
    def from_state(cls, st, cfg, trail, entry_at="close", rank_by="roc"):
        e = cls(cfg, trail, entry_at, rank_by)
        nan = lambda v: np.nan if v is None else v
        e.cash = st["cash"]
        e.last_date = pd.Timestamp(st["last_date"]) if st.get("last_date") else None

        def load_row(r):
            r = {k: (pd.Timestamp(v) if k in DATE_KEYS and v else (pd.NaT if k in DATE_KEYS else nan(v)))
                 for k, v in r.items()}
            return r

        for s, p in st["positions"].items():
            p = dict(p)
            p["entry_date"] = pd.Timestamp(p["entry_date"])
            if p.get("t1_date"):
                p["t1_date"] = pd.Timestamp(p["t1_date"])
            p["eb"] = {k: nan(v) for k, v in p["eb"].items()}
            for k in ("t1_raw_px", "t1_px", "t1_level"):
                if k in p:
                    p[k] = nan(p[k])
            e.pos[s] = p
        e.trades = [load_row(t) for t in st["trades"]]
        e.ledger = [load_row(r) for r in st["ledger"]]
        e.equity = {pd.Timestamp(d): v for d, v in st["equity"].items()}
        e.n_open = {pd.Timestamp(d): v for d, v in st["n_open"].items()}
        return e

    @classmethod
    def load(cls, path):
        with open(path) as f:
            st = json.load(f)
        c = st["config"]
        return cls.from_state(st, c["cfg"], c["trail"], c["entry_at"], c["rank_by"]), st


def simulate(P, cfg, trail, trade_start, entry_at="close", rank_by="roc"):
    """Full-history backtest. Same signature/outputs as the notebook's simulate()."""
    start_i = int(np.searchsorted(P["dates"].values, np.datetime64(trade_start)))
    e = Engine(cfg, trail, entry_at, rank_by).bind(P).run(start_i)
    trades, eq, n_open, ledger = e.results()
    return trades, eq, n_open, ledger, e


def metrics(eq, tr, n_open, cfg):
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = ((eq.iloc[-1] / cfg["capital"]) ** (1 / yrs) - 1) * 100 if yrs > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min() * 100
    r = eq.pct_change().dropna()
    out = dict(start_capital=cfg["capital"], final_equity=round(eq.iloc[-1]),
               total_return_pct=round((eq.iloc[-1] / cfg["capital"] - 1) * 100, 1),
               CAGR_pct=round(cagr, 2), max_DD_pct=round(dd, 2), Calmar=round(cagr / abs(dd), 2) if dd < 0 else np.nan,
               Sharpe=round(r.mean() / r.std() * np.sqrt(252), 2) if len(r) > 1 and r.std() > 0 else np.nan, trades=len(tr))
    if len(tr):
        w, l = tr[tr.pnl > 0], tr[tr.pnl <= 0]
        out.update(win_rate_pct=round(len(w) / len(tr) * 100, 1),
                   profit_factor=round(w.pnl.sum() / abs(l.pnl.sum()), 2) if len(l) and l.pnl.sum() else np.inf,
                   expectancy_pct=round(tr.ret_pct.mean(), 2), avg_win_pct=round(w.ret_pct.mean(), 2) if len(w) else np.nan,
                   avg_loss_pct=round(l.ret_pct.mean(), 2) if len(l) else np.nan, avg_days=round(tr.days.mean(), 1),
                   target_hit_pct=round(tr.hit_target.mean() * 100, 1), avg_slots_used=round(n_open.mean(), 2))
    return out
