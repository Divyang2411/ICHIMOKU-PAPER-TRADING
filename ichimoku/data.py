"""Nifty 200 universe and daily OHLC data (yfinance, cached and incrementally refreshed)."""
import io
import os
import time

import numpy as np
import pandas as pd
import requests

from . import config as C

NSE_LIST_URL = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"


def load_nifty200_symbols(refresh=True):
    """Current official NSE constituents (saved to nifty200.txt); falls back to that file when NSE is unreachable."""
    if not refresh and os.path.exists(C.SYMBOLS_FILE):
        return [s.strip() for s in open(C.SYMBOLS_FILE) if s.strip() and not s.startswith("#")]
    try:
        r = requests.get(NSE_LIST_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        syms = pd.read_csv(io.StringIO(r.text))["Symbol"].str.strip().tolist()
        with open(C.SYMBOLS_FILE, "w") as f:
            f.write("\n".join(syms) + "\n")
        return syms
    except Exception as e:
        print("NSE download failed:", e)
    if os.path.exists(C.SYMBOLS_FILE):
        return [s.strip() for s in open(C.SYMBOLS_FILE) if s.strip() and not s.startswith("#")]
    raise RuntimeError("Put nifty200.txt (one NSE symbol per line) in the project root.")


def _clean(df):
    df = df.rename(columns=str.title)[["Open", "High", "Low", "Close"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")]
    return df.sort_index()


def _download_one(ticker, start, end, retries=3):
    import yfinance as yf
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            raw = yf.Ticker(ticker).history(start=start, end=end, interval="1d",
                                            auto_adjust=(C.PRICE_MODE == "adjusted"), actions=False, timeout=20)
            if raw is not None and len(raw):
                return _clean(raw), None
            last_err = "empty result"
        except Exception as e:
            last_err = str(e)
        if attempt < retries:
            time.sleep(2.0 * attempt)
    return None, last_err


def load_data(symbols, refresh=True, verbose=True):
    """Return {symbol: OHLC DataFrame}.

    First run downloads full history from DATA_START one symbol at a time (the
    batch/threaded yfinance path is unreliable). Later runs re-use the cache and
    only fetch the last few weeks per symbol, so the daily paper-trading run is fast.
    """
    os.makedirs(C.DATA_DIR, exist_ok=True)
    data = pd.read_pickle(C.CACHE_FILE) if os.path.exists(C.CACHE_FILE) else {}
    if not refresh:
        return {s: data[s] for s in symbols if s in data}

    failed = []
    todo = list(symbols)
    if verbose:
        print(f"Updating {len(todo)} symbols ({sum(s in data for s in todo)} cached)...")
    try:
        for n, s in enumerate(todo, 1):
            old = data.get(s)
            # Refetch a 30-day overlap so a corrected/late bar replaces the cached one.
            start = C.DATA_START if old is None or old.empty else (old.index[-1] - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
            new, err = _download_one(f"{s}.NS", start, C.END)
            if new is None:
                failed.append((s, err))
            else:
                if old is not None and C.PRICE_MODE == "adjusted":
                    # adjusted history can be rescaled on dividends: take the fresh full series instead
                    new_full, _ = _download_one(f"{s}.NS", C.DATA_START, C.END)
                    data[s] = new_full if new_full is not None else new
                else:
                    data[s] = new if old is None else pd.concat([old[old.index < new.index[0]], new])
            if n % 20 == 0:
                pd.to_pickle(data, C.CACHE_FILE)
                if verbose:
                    print(f"  [{n}/{len(todo)}] failed={len(failed)}")
            time.sleep(0.1)
    finally:
        pd.to_pickle(data, C.CACHE_FILE)
    if failed and verbose:
        print(f"{len(failed)} symbol(s) had no data:", ", ".join(s for s, _ in failed[:25]))
    return {s: data[s] for s in symbols if s in data}


def synthetic_data(n_symbols=60, start="2014-01-01", end="2026-09-25", seed=7):
    """Random-walk OHLC data with regime drift, used for offline tests and the demo.

    It has NO relation to real market prices; never read performance off it.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    out = {}
    for k in range(n_symbols):
        n = len(dates)
        regime = np.repeat(rng.normal(0.0004, 0.0012, n // 120 + 1), 120)[:n]
        ret = regime + rng.normal(0, 0.017, n)
        close = 100 * rng.uniform(0.5, 20) * np.exp(np.cumsum(ret))
        opn = close * np.exp(rng.normal(0, 0.006, n))
        opn[1:] = close[:-1] * np.exp(rng.normal(0, 0.006, n - 1))
        hi = np.maximum(opn, close) * np.exp(np.abs(rng.normal(0, 0.009, n)))
        lo = np.minimum(opn, close) * np.exp(-np.abs(rng.normal(0, 0.009, n)))
        out[f"SYN{k:03d}"] = pd.DataFrame({"Open": opn, "High": hi, "Low": lo, "Close": close}, index=dates)
    return out
