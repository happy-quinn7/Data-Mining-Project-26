import numpy as np
import pandas as pd

from detector import KNNDetector
from features import all_features, features
from similarity import shared_relative

# Compare the typing to everyone's detector.
class Identifier:

    def __init__(self, window=200, k=3, min_count=2, distance=shared_relative):
        self.window, self.k, self.min_count, self.distance = window, k, min_count, distance

    # Build one detector per person from their typing
    def fit(self, keystrokes):
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

    # Rank everyone by how close they are to the new typing, closest first.
    def rank(self, keystrokes):     # Infinity means too little typing to compare yet.
        window = {"participant": None, "session": None, "index": 0,
                  "keys": keystrokes.sort_values("t_down")}
        f = features(window, self.min_count)
        scores = pd.Series({pid: det.score(f) for pid, det in self.detectors.items()},
                           name="distance").rename_axis("participant")
        return scores.sort_values(kind="stable").reset_index()


# Train on earlier sessions, guess who typed each later window.
def identification_accuracy(feats, train_sessions=("S1", "S2"), test_sessions=("S3",)):
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
