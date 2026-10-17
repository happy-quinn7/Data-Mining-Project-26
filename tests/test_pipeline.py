import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "analysis"), str(ROOT / "tools")]

import clean_raw
from loading import load_session, load_all
from features import make_windows, features, all_features
from similarity import shared_mae, shared_relative, rank_distance, INF
from detector import KNNDetector, ProfileDetector, OutlierCountDetector
from evaluation import score_all, far_frr, eer

TEXT = "le chat est sur le mur et le chien est dans le jardin "


def typed(text, pid="P01", sid="S1", hold=100, gap=150, start=1_700_000_000_000.0,
          sample=0, first_id=1, jitter=0, seed=0):
    rng = np.random.default_rng(seed)
    rows, t = [], start
    for i, ch in enumerate(text):
        pid_ = first_id + i
        code = "Space" if ch == " " else f"Key{ch.upper()}"
        h = hold + rng.normal(0, jitter)
        rows.append([pid, sid, sample, "down", code, t, pid_, ch])
        rows.append([pid, sid, sample, "up", code, t + h, pid_, ch])
        t += gap + rng.normal(0, jitter)
    return rows


def write(rows, path):
    df = pd.DataFrame(rows, columns=clean_raw.COLUMNS).sort_values("timestamp_ms", kind="stable")
    df.to_csv(path, index=False)
    return path

def test_clean_keeps_name_and_format(tmp_path):
    src = write(typed(TEXT, pid="Po1"), tmp_path / "P01_S1.csv")
    out = tmp_path / "out"
    clean_raw.main(src, out)
    df = pd.read_csv(out / "P01_S1.csv")
    assert list(df.columns) == clean_raw.COLUMNS
    assert set(df.participant_id) == {"P01"}
    assert set(df.session_id) == {"S1"}


def test_clean_refuses_bad_names(tmp_path):
    for bad in ["P01_S4.csv", "p01_s1.csv", "P01-S1.csv", "data.csv"]:
        with pytest.raises(ValueError):
            clean_raw.clean(write(typed(TEXT), tmp_path / bad))


def test_clean_keeps_longest_recording(tmp_path):
    main = typed(TEXT * 3)
    test = typed("abc", start=main[-1][5] + 60_000)
    df, _ = clean_raw.clean(write(main + test, tmp_path / "P01_S1.csv"))
    assert (df.event == "down").sum() == len(TEXT * 3)


def test_clean_orphans(tmp_path):
    rows = typed(TEXT)
    del rows[1]
    rows.append(["P01", "S1", 0, "up", "KeyZ", rows[-1][5] + 5, 999, "z"])
    df, _ = clean_raw.clean(write(rows, tmp_path / "P01_S1.csv"))
    assert 999 not in set(df.press_id)
    assert 1 in set(df[df.event == "down"].press_id)

def test_hold_and_latency_values(tmp_path):
    k = load_session(write(typed(TEXT, hold=90, gap=140), tmp_path / "P01_S1.csv"))
    assert np.allclose(k.hold.dropna(), 90)
    assert np.allclose(k.latency.dropna(), 140)
    assert np.allclose(k.flight.dropna(), 50)
    assert (k.digraph.dropna() == "le").sum() == 4


def test_overlap_gives_negative_flight(tmp_path):
    k = load_session(write(typed(TEXT, hold=200, gap=120), tmp_path / "P01_S1.csv"))
    assert (k.flight.dropna() < 0).all()


def test_shift_and_case(tmp_path):
    rows = typed("Le", hold=80, gap=150)
    t0 = rows[0][5]
    rows = [["P01", "S1", 0, "down", "ShiftLeft", t0 - 50, 100, "Shift"]] + rows
    rows[2][7] = "l"
    rows.insert(2, ["P01", "S1", 0, "up", "ShiftLeft", t0 + 40, 100, "Shift"])
    k = load_session(write(rows, tmp_path / "P01_S1.csv"))
    assert k[k.key_code == "KeyL"].char.item() == "l"
    assert k.digraph.dropna().tolist() == ["le"]


def test_no_digraph_across_backspace_sample_or_pause(tmp_path):
    a = typed("ab", gap=150)
    bs = [["P01", "S1", 0, "down", "Backspace", a[-1][5] + 150, 3, "Backspace"],
          ["P01", "S1", 0, "up", "Backspace", a[-1][5] + 230, 3, "Backspace"]]
    c = typed("cd", start=bs[-1][5] + 150, first_id=4)
    d = typed("ef", start=c[-1][5] + 150, first_id=6, sample=1)
    e = typed("gh", start=d[-1][5] + 5000, first_id=8, sample=1)
    k = load_session(write(a + bs + c + d + e, tmp_path / "P01_S1.csv"))
    assert sorted(k.digraph.dropna()) == ["ab", "cd", "ef", "gh"]


def test_no_digraph_across_lost_press(tmp_path):
    rows = typed("the cat sat on the mat", gap=150)
    del rows[2:4]
    k = load_session(write(rows, tmp_path / "P01_S1.csv"))
    d = k.digraph.dropna().tolist()
    assert "te" not in d
    assert d.count("th") == 1
    assert k.lost_after.sum() == 1
    assert np.allclose(k.latency.dropna(), 150)


def test_bad_hold_removed(tmp_path):
    k = load_session(write(typed(TEXT, hold=2000, gap=2100), tmp_path / "P01_S1.csv"))
    assert k.hold.isna().all()


def test_load_all_reads_only_session_files(tmp_path):
    write(typed(TEXT), tmp_path / "P01_S1.csv")
    write(typed(TEXT, sid="S2"), tmp_path / "P01_S2.csv")
    write(typed(TEXT), tmp_path / "notes.csv")
    k = load_all(tmp_path)
    assert sorted(k.session.unique()) == ["S1", "S2"]

def keystrokes(tmp_path, **kw):
    name = f"{kw.get('pid', 'P01')}_{kw.get('sid', 'S1')}.csv"
    return load_session(write(typed(TEXT * 20, **kw), tmp_path / name))


def test_windows(tmp_path):
    k = keystrokes(tmp_path)
    w = make_windows(k, n=100)
    assert len(w) == len(k) // 100
    assert all(len(x["keys"]) == 100 for x in w)
    assert len(make_windows(k, n=100, step=50)) == (len(k) - 100) // 50 + 1


def test_features_values(tmp_path):
    f = features(make_windows(keystrokes(tmp_path, hold=90, gap=140), n=200)[0])
    assert np.allclose(f["digraph"], 140)
    assert np.allclose(f["hold"], 90)
    assert "le" in f["digraph"].index


def test_distances(tmp_path):
    fast = all_features(keystrokes(tmp_path, gap=120, jitter=5), n=200)
    slow = all_features(keystrokes(tmp_path, gap=240, jitter=5, sid="S2"), n=200)
    for d in (shared_mae, shared_relative, rank_distance):
        assert d(fast[0], fast[0]) == 0
        assert d(fast[0], fast[1]) < d(fast[0], slow[0]) or d is rank_distance
    empty = {"digraph": pd.Series(dtype=float), "hold": pd.Series(dtype=float)}
    assert shared_mae(fast[0], empty) == INF

def test_detectors_separate_two_different_typists(tmp_path):
    k = pd.concat([keystrokes(tmp_path, pid=p, sid=s, gap=g, hold=h, jitter=8, seed=i)
                   for i, (p, g, h) in enumerate([("P01", 120, 80), ("P02", 220, 130)])
                   for s in ("S1", "S2", "S3")], ignore_index=True)
    feats = all_features(k, n=200)
    for make in (lambda: KNNDetector(k=3), ProfileDetector, OutlierCountDetector):
        scores = score_all(feats, make)
        assert scores.genuine.any() and (~scores.genuine).any()
        assert eer(far_frr(scores))[0] == 0.0


def test_far_frr_extremes():
    s = pd.DataFrame({"genuine": [True, True, False, False], "score": [1, 2, 3, 4]})
    c = far_frr(s)
    assert c.iloc[0][["FAR", "FRR"]].tolist() == [0, 1]
    assert c.iloc[-1][["FAR", "FRR"]].tolist() == [1, 0]