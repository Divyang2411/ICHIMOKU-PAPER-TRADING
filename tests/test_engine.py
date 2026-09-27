import json

import numpy as np
import pandas as pd
import pytest

from ichimoku import config as C
from ichimoku.data import synthetic_data
from ichimoku.engine import Engine, simulate
from ichimoku.indicators import build_panel
from tests import reference_notebook as ref


@pytest.fixture(scope="module")
def panel():
    return build_panel(synthetic_data(n_symbols=40, seed=11), C.CFG, C.STRATEGY)


TRADE_COLS = ["symbol", "entry_date", "exit_date", "entry_px", "exit_px", "qty", "invested", "pnl", "reason", "t1_qty", "t1_px", "days"]


@pytest.mark.parametrize("entry_at", ["close", "next_open"])
@pytest.mark.parametrize("trail", [C.TRAIL, dict(kind="atr", value=3, be=False), dict(kind="kijun", value=0, be=True)])
def test_matches_notebook(panel, entry_at, trail):
    t0, eq0, n0, l0 = ref.simulate(panel, C.CFG, trail, entry_at=entry_at)
    t1, eq1, n1, l1, _ = simulate(panel, C.CFG, trail, ref.TRADE_START, entry_at=entry_at)
    assert len(t0) > 20
    pd.testing.assert_series_equal(eq0, eq1, check_names=False, check_freq=False)
    pd.testing.assert_series_equal(n0, n1, check_names=False, check_freq=False, check_dtype=False)
    pd.testing.assert_frame_equal(t0[TRADE_COLS].reset_index(drop=True), t1[TRADE_COLS].reset_index(drop=True), check_dtype=False)
    assert len(l0) == len(l1)


@pytest.mark.parametrize("entry_at", ["close", "next_open"])
def test_resume_from_json_is_identical(panel, entry_at, tmp_path):
    """Paper trading saves state every day; stopping/resuming must not change a single trade."""
    start = int(np.searchsorted(panel["dates"].values, np.datetime64("2019-01-01")))
    full = Engine(C.CFG, C.TRAIL, entry_at).bind(panel).run(start)

    e = Engine(C.CFG, C.TRAIL, entry_at).bind(panel)
    cuts = [start + 250, start + 251, start + 600, start + 1100]
    i = start
    for cut in cuts + [len(panel["dates"])]:
        e.run(i, cut)
        path = tmp_path / "state.json"
        e.save(path)
        json.loads(path.read_text())            # valid JSON
        e, _ = Engine.load(path)
        e.bind(panel)
        i = cut

    a, b = full.results(), e.results()
    pd.testing.assert_series_equal(a[1], b[1], check_freq=False)
    cols = [c for c in TRADE_COLS if c != "t1_px"]
    pd.testing.assert_frame_equal(a[0][cols], b[0][cols], check_dtype=False)
    np.testing.assert_allclose(a[0]["t1_px"].astype(float), b[0]["t1_px"].astype(float))
