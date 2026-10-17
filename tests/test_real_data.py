import re
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from loading import load_all, quality_report   # noqa: E402

DATA = ROOT / "data" / "processed"
FILES = sorted(DATA.glob("*.csv")) if DATA.exists() else []
pytestmark = pytest.mark.skipif(not FILES, reason="no files in data/processed")


class DataQualityWarning(UserWarning):
    pass


def warn_if(table, message):
    if not table.empty:
        names = ", ".join(f"{p}_{s}: {v}" for (p, s), v in table.items())
        warnings.warn(f"{message} -> {names}", DataQualityWarning)


@pytest.fixture(scope="module")
def k():
    return load_all(DATA)


@pytest.fixture(scope="module")
def q(k):
    return quality_report(k)

def test_file_names():
    bad = [f.name for f in FILES if not re.fullmatch(r"P\d+_S[123]\.csv", f.name)]
    assert not bad, f"wrong names: {bad}"


def test_timestamps_increase(k):
    for (p, s), g in k.groupby(["participant", "session"]):
        assert g.t_down.is_monotonic_increasing, f"{p}_{s}: presses out of order"

def test_enough_keystrokes(q):
    warn_if(q.presses[q.presses < 2000], "fewer than 2000 keystrokes")


def test_hold_times_plausible(k):
    med = k.groupby(["participant", "session"]).hold.median().round(1)
    warn_if(med[(med < 40) | (med > 250)], "median hold time outside 40-250 ms")


def test_few_lost_presses(q):
    warn_if(q.lost_presses[q.lost_presses > 0.10],
            "collector lost more than 10% of presses (share of presses followed by a lost one)")


def test_few_missing_holds(q):
    warn_if(q.hold_missing[q.hold_missing > 0.10],
            "more than 10% of presses without a usable hold time (release lost or hold outside 10-500 ms)")


def test_enough_digraphs(q):
    warn_if(q.digraphs[q.digraphs < 1000], "fewer than 1000 usable digraphs")


def test_three_sessions_per_participant(k):
    sessions = k.groupby("participant").session.unique().map(sorted)
    incomplete = sessions[sessions.map(len) < 3]
    if not incomplete.empty:
        pytest.skip(f"not all sessions collected yet: {incomplete.to_dict()}")