"""Strategy and run configuration.

The values mirror ICHIMOKU_FINAL_BACKTEST_2.ipynb so that the backtest, the
daily scanner and the paper-trading engine all trade the exact same rules.
"""
import os

CFG = dict(
    tenkan=9, kijun=26, senkou_b=52, displacement=26,
    capital=500_000, max_positions=10, alloc_pct=10,     # alloc_pct of CURRENT equity -> compounding
    sl_pct=12, tp_pct=24, book_fraction=0.5,             # SL 12%, T1 target +24% (book 50%)
    commission_pct=0.10, slippage_ticks=0, tick_size=0.05,
)
STRATEGY = ("price_kijun", "strong")                     # close crosses above Kijun while above the cloud
TRAIL = dict(kind="pct", value=30, be=True)              # after T1: 30% trail from peak, never below entry
ENTRY_AT = "close"                                       # "close" or "next_open"
RANK_BY = "roc"                                          # 26-day rate of change, strongest first

DATA_START, TRADE_START, END = "2014-01-01", "2015-01-01", None
PRICE_MODE = "raw"                                       # "raw" or "adjusted"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
RESULTS_DIR = os.path.join(ROOT, "results")
PAPER_DIR = os.path.join(ROOT, "paper")
CACHE_FILE = os.path.join(DATA_DIR, f"nifty200_prices_{PRICE_MODE}.pkl")
SYMBOLS_FILE = os.path.join(ROOT, "nifty200.txt")
