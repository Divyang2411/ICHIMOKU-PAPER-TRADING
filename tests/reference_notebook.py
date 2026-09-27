"""Verbatim copy of simulate() from ICHIMOKU_FINAL_BACKTEST_2.ipynb (cell 10).

Used only as the reference implementation in tests: ichimoku.engine must
reproduce it trade for trade.
"""
import math

import numpy as np
import pandas as pd

ENTRY_AT, RANK_BY, TRADE_START = "close", "roc", "2015-01-01"


def simulate(P, cfg, trail, entry_at=ENTRY_AT, rank_by=RANK_BY):
    sig = P["sig"]
    O, H, L, C, Cff, KJ, ATR, ROC = (P[k] for k in ["Open", "High", "Low", "Close", "Cff", "kijun", "atr", "roc"])
    dates, syms = P["dates"], P["syms"]
    T, N = C.shape
    slip = cfg["slippage_ticks"] * cfg["tick_size"]; comm = cfg["commission_pct"] / 100
    sl_f, tp_f = 1 - cfg["sl_pct"] / 100, 1 + cfg["tp_pct"] / 100
    start_i = int(np.searchsorted(dates.values, np.datetime64(TRADE_START)))
    cash = float(cfg["capital"]); pos = {}; trades = []; ledger = []
    equity = np.full(T, float(cfg["capital"])); npos = np.zeros(T, int)

    def mark(i): return cash + sum(p["qty"] * Cff[i, s] for s, p in pos.items())
    def bar(i, s): return dict(open=O[i, s], high=H[i, s], low=L[i, s], close=C[i, s])

    def sell(p, qty, raw_fill, i, reason, rule, level=np.nan):
        nonlocal cash
        assert np.isfinite(raw_fill), f"non-finite exit fill {syms[p['s']]} {dates[i]}"
        px = raw_fill if slip == 0 else raw_fill - slip          # slip == 0 -> execution price is the untouched fill
        cash += qty * px * (1 - comm); p["proceeds"] += qty * px * (1 - comm); p["qty"] -= qty
        b = bar(i, p["s"])
        ledger.append(dict(symbol=syms[p["s"]], date=dates[i], action="EXIT_" + reason, qty=qty, raw_px=raw_fill, fill_px=px,
                           open=b["open"], high=b["high"], low=b["low"], close=b["close"], rule=rule, level=level))
        if reason in ("T1",):
            p.update(t1_date=dates[i], t1_raw_px=raw_fill, t1_px=px, t1_qty=qty, t1_level=level)
        if p["qty"] == 0:
            pnl = p["proceeds"] - p["cost"]
            trades.append(dict(symbol=syms[p["s"]], entry_date=dates[p["entry_i"]], exit_date=dates[i],
                               entry_px=p["entry_px"], exit_px=px, entry_raw_px=p["entry_raw_px"], exit_raw_px=raw_fill,
                               qty=p["qty0"], invested=p["cost"], pnl=pnl, ret_pct=pnl / p["cost"] * 100,
                               hit_target=p["t1"], reason=reason, peak_gain_pct=(p["peak"] / p["entry_px"] - 1) * 100, days=i - p["entry_i"],
                               entry_open=p["eb"]["open"], entry_high=p["eb"]["high"], entry_low=p["eb"]["low"], entry_close=p["eb"]["close"],
                               exit_open=b["open"], exit_high=b["high"], exit_low=b["low"], exit_close=b["close"],
                               fill_rule=rule, exit_level=level, sl_px=p["sl_px"], tp_px=p["tp_px"],
                               t1_date=p.get("t1_date", pd.NaT), t1_raw_px=p.get("t1_raw_px", np.nan), t1_px=p.get("t1_px", np.nan),
                               t1_qty=p.get("t1_qty", 0)))
            del pos[p["s"]]

    def try_enter(row, price_row, eq, i):
        nonlocal cash
        free = cfg["max_positions"] - len(pos)
        if free <= 0: return
        cand = [s for s in np.flatnonzero(sig[row] & ~np.isnan(price_row)) if s not in pos]
        if not cand: return
        if rank_by == "roc":
            cand.sort(key=lambda s: -(ROC[row, s] if not np.isnan(ROC[row, s]) else -9))
        for s in cand[:free]:
            raw = price_row[s]                                    # exact execution price from the simulator's own OHLC array
            assert np.isfinite(raw), f"non-finite entry price {syms[s]} {dates[i]}"
            px = raw if slip == 0 else raw + slip                 # slip == 0 -> px IS raw
            q = min(math.floor(cfg["alloc_pct"] / 100 * eq / (px * (1 + comm))), math.floor(cash / (px * (1 + comm))))
            if q < 1: continue
            cost = q * px * (1 + comm); cash -= cost
            b = bar(i, s)
            pos[s] = dict(s=s, entry_i=i, entry_px=px, entry_raw_px=raw, eb=b, qty0=q, qty=q, cost=cost, proceeds=0.0, t1=False,
                          peak=px, sl_px=px * sl_f, tp_px=px * tp_f)
            ledger.append(dict(symbol=syms[s], date=dates[i], action="ENTRY", qty=q, raw_px=raw, fill_px=px,
                               open=b["open"], high=b["high"], low=b["low"], close=b["close"],
                               rule="close" if entry_at == "close" else "open", level=np.nan))

    for i in range(start_i, T):
        if entry_at == "next_open" and i > start_i:
            try_enter(i - 1, O[i], equity[i - 1], i)
        for s in list(pos):
            p = pos[s]; o, h, l, c = O[i, s], H[i, s], L[i, s], C[i, s]
            if np.isnan(h) or np.isnan(l) or np.isnan(o): continue
            if not p["t1"]:
                if l <= p["sl_px"]:
                    sell(p, p["qty"], min(o, p["sl_px"]), i, "SL", "min(open,sl_px)", p["sl_px"])
                elif h >= p["tp_px"]:
                    fill = max(o, p["tp_px"])
                    half = int(round(p["qty0"] * cfg["book_fraction"]))
                    if p["qty0"] < 2: sell(p, p["qty"], fill, i, "TARGET_FULL", "max(open,tp_px)", p["tp_px"]); continue
                    half = min(max(half, 1), p["qty0"] - 1)
                    sell(p, half, fill, i, "T1", "max(open,tp_px)", p["tp_px"]); p["t1"] = True; p["peak"] = max(p["peak"], h)
                else:
                    p["peak"] = max(p["peak"], h)
            else:
                lvl = -np.inf
                if trail["kind"] == "pct":
                    lvl = p["peak"] * (1 - trail["value"] / 100)
                elif trail["kind"] == "atr" and not np.isnan(ATR[i - 1, s]):
                    lvl = p["peak"] - trail["value"] * ATR[i - 1, s]
                lvl = max(lvl, p["sl_px"])
                if trail.get("be", False): lvl = max(lvl, p["entry_px"])
                if l <= lvl:
                    sell(p, p["qty"], min(o, lvl), i, "TRAIL", "min(open,trail_level)", lvl)
                elif trail["kind"] == "kijun" and not np.isnan(KJ[i, s]) and c < KJ[i, s]:
                    sell(p, p["qty"], c, i, "TRAIL", "close", KJ[i, s])
                else:
                    p["peak"] = max(p["peak"], h)
        if entry_at == "close":
            try_enter(i, C[i], mark(i), i)
        equity[i] = mark(i); npos[i] = len(pos)

    for s, p in list(pos.items()):      # still open at the end: mark-to-market at last close (equity already reflects it)
        last = Cff[T - 1, s]; val = p["qty"] * last * (1 - comm) + p["proceeds"]
        b = bar(T - 1, s)
        trades.append(dict(symbol=syms[s], entry_date=dates[p["entry_i"]], exit_date=dates[T - 1],
                           entry_px=p["entry_px"], exit_px=last, entry_raw_px=p["entry_raw_px"], exit_raw_px=last,
                           qty=p["qty0"], invested=p["cost"], pnl=val - p["cost"], ret_pct=(val - p["cost"]) / p["cost"] * 100,
                           hit_target=p["t1"], reason="OPEN", peak_gain_pct=(p["peak"] / p["entry_px"] - 1) * 100, days=T - 1 - p["entry_i"],
                           entry_open=p["eb"]["open"], entry_high=p["eb"]["high"], entry_low=p["eb"]["low"], entry_close=p["eb"]["close"],
                           exit_open=b["open"], exit_high=b["high"], exit_low=b["low"], exit_close=b["close"],
                           fill_rule="last_close(MTM)", exit_level=np.nan, sl_px=p["sl_px"], tp_px=p["tp_px"],
                           t1_date=p.get("t1_date", pd.NaT), t1_raw_px=p.get("t1_raw_px", np.nan), t1_px=p.get("t1_px", np.nan),
                           t1_qty=p.get("t1_qty", 0)))
    eq = pd.Series(equity[start_i:], index=dates[start_i:]); n_open = pd.Series(npos[start_i:], index=dates[start_i:])
    return pd.DataFrame(trades), eq, n_open, pd.DataFrame(ledger)

