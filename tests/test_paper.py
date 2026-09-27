import numpy as np
import pandas as pd

from ichimoku import config as C
from ichimoku import paper
from ichimoku.data import synthetic_data
from ichimoku.engine import Engine
from ichimoku.indicators import build_panel


def test_daily_paper_runs_match_one_backtest(tmp_path, monkeypatch):
    """Running the paper trader day by day (data growing one bar at a time) == one engine run."""
    monkeypatch.setattr(C, "PAPER_DIR", str(tmp_path))
    monkeypatch.setattr(paper, "STATE_FILE", str(tmp_path / "state.json"))
    full = synthetic_data(n_symbols=30, seed=3)
    dates = next(iter(full.values())).index
    start = "2025-06-02"
    paper.init(start_date=start)

    days = dates[dates >= start]
    checkpoints = list(days[5::37]) + [days[-1]]
    for d in checkpoints:
        paper.run(data={s: df.loc[:d] for s, df in full.items()}, verbose=False)
    e, _ = Engine.load(paper.STATE_FILE)

    P = build_panel(full, C.CFG, C.STRATEGY)
    ref = Engine(C.CFG, C.TRAIL, C.ENTRY_AT, C.RANK_BY).bind(P)
    ref.run(int(np.searchsorted(P["dates"].values, np.datetime64(start))))

    assert len(ref.trades) > 3
    assert [t["symbol"] for t in e.trades] == [t["symbol"] for t in ref.trades]
    np.testing.assert_allclose([t["pnl"] for t in e.trades], [t["pnl"] for t in ref.trades])
    assert sorted(e.pos) == sorted(ref.pos)
    np.testing.assert_allclose(e.cash, ref.cash)
    assert (tmp_path / "positions.csv").exists() and (tmp_path / "journal.md").exists()
