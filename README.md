# Ichimoku Nifty 200: Scanner, Backtest and Paper Trading

A complete system for the Ichimoku strategy from `notebooks/ICHIMOKU_FINAL_BACKTEST_2.ipynb`:

1. **Scanner**: scans every Nifty 200 stock each day and lists buy signals, near-signals and an Ichimoku health score.
2. **Backtest**: runs 2015 to today with ₹5,00,000 and writes an interactive report with the equity curve, drawdown, yearly and monthly returns, and every trade's entry, exit, SL, target and P&L on a candlestick chart with the cloud.
3. **Paper trading**: forward-tests the same rules on live daily data with a ₹5,00,000 virtual account. State is saved in `paper/`, and a GitHub Action can run it automatically after every market close.

All three use one engine (`ichimoku/engine.py`), so the paper trader follows the backtest rules exactly.

## Strategy rules

| Rule | Value |
|---|---|
| Universe | Nifty 200 (list refreshed from NSE on each run) |
| Entry signal | Close crosses **above Kijun-sen** while the close is **above the Kumo cloud** (`price_kijun / strong`) |
| Entry fill | Signal day's close |
| Ranking | When there are more signals than free slots, take the highest 26-day ROC first |
| Position size | 10% of **current** equity per trade (compounding), max 10 positions |
| Stop-loss | −12% from entry (fills at the open if the stock gaps below it) |
| Target T1 | +24%: book 50% of the position |
| After T1 | Trail the rest 30% below the highest high, never below entry (breakeven) |
| Costs | 0.10% commission per side, zero slippage |

You can change any of these in `ichimoku/config.py`.

### Your notebook's result (real Nifty 200 data, 2015-01-01 → 2026-09-25)

These numbers come from the output of the attached notebook run, not from this repo:

| Final equity | Total return | CAGR | Max DD | Sharpe | Trades | Win rate | Profit factor |
|---|---|---|---|---|---|---|---|
| ₹76,68,562 | +1,433.7% | 26.2% | −28.3% (Feb→May 2020) | 1.56 | 132 | 52.3% | 5.25 |

`python main.py backtest` recreates this run. The engine is tested to match the notebook's `simulate()` trade for trade (see *Tests*).

## Setup

```bash
pip install -r requirements.txt
```

The first run downloads about 12 years of daily data for 200 stocks from Yahoo Finance, which takes 5–10 minutes. The data is cached in `data/`, and later runs only fetch the last few weeks.

## Usage

```bash
# 1. Backtest -> results/backtest/report.html (+ trades.csv, executions.csv, equity_curve.csv, metrics.csv, equity_summary.png)
python main.py backtest

# 2. Today's scan -> results/scan_latest.csv
python main.py scan

# 3. Paper trading
python main.py paper init                 # new ₹5,00,000 account starting today
python main.py paper run                  # run once a day after 15:30 IST
python main.py paper status               # positions, stops, equity (no download)
python main.py paper report               # paper/report.html
```

Useful flags:

- `paper init --start 2026-01-01`: start the paper account on a past date. The first `run` replays every day since then.
- `paper init --capital 1000000`: use a different starting capital.
- `--offline`: use cached prices only.
- `--limit 20`: only the first 20 symbols, for a quick test.
- `--demo`: use synthetic random prices with no internet. This is only for checking that everything works; the numbers mean nothing.

### What a daily paper run does

Each run processes every new bar since the last run, in the same order the backtest uses:

1. **Exits** on held stocks:
   - SL hit → sell everything.
   - Target hit → sell 50% and switch to the trailing stop.
   - Trail or breakeven hit → sell the rest.
2. **Entries** at the close: new signals ranked by ROC fill the free slots, each sized at 10% of equity.
3. **Saved files**: state and all outputs go to `paper/`:
   - `state.json`: the full account, which is the source of truth
   - `positions.csv`: holdings with today's stop-loss, T1 target and stage
   - `trades.csv`, `executions.csv`, `equity.csv`
   - `journal.md`: one line per execution
   - `scan_latest.csv`

If you run it before 15:45 IST, today's unfinished bar is skipped. A missed day is not a problem: the next run catches up bar by bar.

### Automatic daily run (GitHub Actions)

`.github/workflows/paper-trade.yml` runs on weekdays at 16:17 IST:

- It opens the account on its first run.
- It processes the day's bars and commits `paper/` back to the repo.
- It uploads `paper/report.html` as a build artifact.

To use it, enable Actions for the repo. You can also start it by hand from the Actions tab with **Run workflow**; tick *backtest* to get the full backtest report as well.

## The report

`report.html` is one self-contained file that works offline. Open it in a browser. It includes:

- Summary tiles: final equity, return, CAGR, max drawdown, Sharpe, win rate, profit factor, average win and loss, and commission paid.
- Equity curve (log or linear) against the Nifty 50, plus the drawdown and the number of open positions.
- Return by year, a monthly returns heatmap and a year-by-year table.
- A P&L breakdown by exit reason and a histogram of trade returns.
- **A trade chart:** pick any trade to see its candles with Tenkan, Kijun and the cloud, and markers for entry ▲, T1 ◆ and exit ▼, with the SL and target lines.
- A sortable, filterable table of all trades: entry, qty, SL, target, T1 leg, exit, reason, P&L and return.
- The Nifty 200 scan table.
- Paper reports also show open positions with their live stop and target, and the latest executions.

## Project layout

```
main.py                     CLI: backtest | scan | paper
ichimoku/config.py          strategy parameters
ichimoku/data.py            Nifty 200 list + yfinance download/cache (+ synthetic demo data)
ichimoku/indicators.py      Ichimoku lines, entry signals, price panel
ichimoku/engine.py          portfolio engine (backtest + paper), metrics, JSON state
ichimoku/scanner.py         daily scan
ichimoku/paper.py           paper-trading driver
ichimoku/report.py          HTML/PNG report
tests/                      engine == notebook; paper day-by-day == backtest
notebooks/                  original research notebook
```

## Tests

```bash
python -m pytest -q
```

- `test_engine.py`: runs the notebook's original `simulate()` (copied verbatim into `tests/reference_notebook.py`) and the new engine on the same data. It checks that equity, positions and every trade are identical for both entry modes and three trail types. It also checks that saving and reloading state mid-run changes nothing.
- `test_paper.py`: feeds the paper trader data that grows over time, checkpoint by checkpoint. It checks that the result is identical to a single backtest over the same period, which proves the paper path has no look-ahead and no drift.

## Caveats

- Yahoo `raw` prices are split-adjusted but not dividend-adjusted, and bars are sometimes revised late.
- The backtest uses today's Nifty 200 list for all of history. That is survivorship bias: stocks that dropped out of the index are missing, so past results are probably better than what was really achievable.
- Paper trading assumes you get filled at the day's close, with no slippage.
- This is a research tool, not investment advice.
