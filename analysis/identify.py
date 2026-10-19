"""Identification: who, among the enrolled participants, is typing?

Verification (detector.py, evaluation.py) asks "is this person who they claim to
be?". Identification asks "which of the known people is this?": the typing is
scored against every participant's KNN detector and the closest one wins.

    ident = Identifier().fit(load_all("data/processed"))
    ident.rank(keystrokes)      # participants sorted from most to least similar
"""
import numpy as np
import pandas as pd

from detector import KNNDetector
from features import all_features, features
from similarity import shared_relative


class Identifier:

    def __init__(self, window=200, k=3, min_count=2, distance=shared_relative):
        self.window, self.k, self.min_count, self.distance = window, k, min_count, distance

    def fit(self, keystrokes):
        """Enroll every participant in a keystroke table (load_all output)."""
        feats = all_features(keystrokes, n=self.window, min_count=self.min_count)
        self.detectors = {}
        for pid in sorted({f["participant"] for f in feats}):
            mine = [f for f in feats if f["participant"] == pid]
            self.detectors[pid] = KNNDetector(self.distance, self.k).fit(mine)
        if not self.detectors:
            raise ValueError(f"no participant has a full {self.window}-keystroke window")
        return self

    @property
    def participants(self):
        return list(self.detectors)

    def rank(self, keystrokes):
        """Distance from this stretch of typing to each participant, closest first.

        `keystrokes` is one person's typing in the load_session format. The
        distance is inf for a participant who shares too few letter pairs with
        it to compare (usually: not enough typing yet).
        """
        window = {"participant": None, "session": None, "index": 0,
                  "keys": keystrokes.sort_values("t_down")}
        f = features(window, self.min_count)
        scores = pd.Series({pid: det.score(f) for pid, det in self.detectors.items()},
                           name="distance").rename_axis("participant")
        return scores.sort_values(kind="stable").reset_index()


def identification_accuracy(feats, train_sessions=("S1", "S2"), test_sessions=("S3",)):
    """Share of test windows whose closest participant is the right one.

    Each participant is enrolled on their train_sessions windows; every window
    of a test session is then identified among all enrolled participants.
    """
    enrolled = {}
    for pid in sorted({f["participant"] for f in feats}):
        train = [f for f in feats if f["participant"] == pid and f["session"] in train_sessions]
        if train:
            enrolled[pid] = KNNDetector(shared_relative, 3).fit(train)
    tests = [f for f in feats if f["session"] in test_sessions and f["participant"] in enrolled]
    if len(enrolled) < 2 or not tests:
        return np.nan, 0
    right = [min(enrolled, key=lambda p: enrolled[p].score(f)) == f["participant"] for f in tests]
    return float(np.mean(right)), len(tests)
