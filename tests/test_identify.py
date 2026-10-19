import csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "frontend"), str(ROOT / "analysis"), str(ROOT / "tools")]

import server
from features import all_features
from identify import Identifier, identification_accuracy
from loading import load_all, load_session
from test_pipeline import TEXT, typed, write

TYPISTS = {"P01": dict(gap=120, hold=80), "P02": dict(gap=220, hold=130), "P03": dict(gap=170, hold=100)}


def enroll(folder, sessions=("S1", "S2")):
    for i, (pid, style) in enumerate(TYPISTS.items()):
        for j, sid in enumerate(sessions):
            write(typed(TEXT * 20, pid=pid, sid=sid, jitter=8, seed=10 * i + j, **style),
                  folder / f"{pid}_{sid}.csv")
    return folder


def page_events(text, pid="P00", **style):
    """What the page sends in guess mode."""
    return [dict(zip(server.COLUMNS, r[:5] + [f"{r[5]:.3f}"] + r[6:]))
            for r in typed(text, pid=pid, sid="S0", jitter=8, seed=99, **style)]


def test_identifier_ranks_the_right_typist(tmp_path):
    ident = Identifier().fit(load_all(enroll(tmp_path)))
    assert ident.participants == ["P01", "P02", "P03"]
    for pid, style in TYPISTS.items():
        k = load_session(write(typed(TEXT * 4, pid="P00", sid="S3", jitter=8, seed=7, **style),
                               tmp_path / "probe.csv"))
        ranking = ident.rank(k)
        assert ranking.participant[0] == pid
        assert ranking.distance.is_monotonic_increasing


def test_too_little_typing_is_not_comparable(tmp_path):
    ident = Identifier().fit(load_all(enroll(tmp_path)))
    k = load_session(write(typed("hello", pid="P00", sid="S3"), tmp_path / "probe.csv"))
    assert np.isinf(ident.rank(k).distance).all()


def test_identification_accuracy(tmp_path):
    k = load_all(enroll(tmp_path, sessions=("S1", "S2", "S3")))
    acc, tests = identification_accuracy(all_features(k, n=200))
    assert acc == 1.0 and tests > 0


def test_server_identify(tmp_path):
    folder = enroll(tmp_path)
    out = server.identify(page_events(TEXT * 4, **TYPISTS["P02"]), folder=folder)
    assert out["ready"] and out["ranking"][0]["participant"] == "P02"
    assert out["enrolled"] == ["P01", "P02", "P03"]
    early = server.identify(page_events("hi there"), folder=folder)
    assert not early["ready"] and all(r["distance"] is None for r in early["ranking"])


def test_server_identify_without_enrolled_participants(tmp_path):
    with pytest.raises(ValueError, match="Nobody is enrolled"):
        server.identify(page_events(TEXT), folder=tmp_path)


def test_feedback_log(tmp_path):
    log = tmp_path / "identify_log.csv"
    server.log_identification({"predicted": "P02", "actual": "P02", "keys": 250, "ranking": []}, log)
    server.log_identification({"predicted": "P02", "actual": "unknown", "keys": 300}, log)
    rows = list(csv.DictReader(open(log, encoding="utf-8")))
    assert [r["correct"] for r in rows] == ["True", "False"]
    for bad in [{"predicted": "x", "actual": "P01", "keys": 1},
                {"predicted": "P01", "actual": "P01", "keys": "many"}]:
        with pytest.raises(ValueError):
            server.log_identification(bad, log)
