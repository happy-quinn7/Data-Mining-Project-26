import numpy as np
import pandas as pd

from similarity import shared_relative

# KNN Detector
class KNNDetector:

    def __init__(self, distance=shared_relative, k=3): # Weight of k=3
        self.distance, self.k = distance, k

    # Stores distance function
    def fit(self, train_feats):
        if not train_feats:
            raise ValueError("no training windows") # If no training windows raise error
        self.train = list(train_feats)
        return self

    def score(self, f):
        d = np.sort([self.distance(f, t) for t in self.train])  # Measures distance from training window f to all stored windows
        return float(np.mean(d[: min(self.k, len(d))]))         # Stored distance from smallest to largest

# Profile Detector
class ProfileDetector:

    def __init__(self, part="digraph", min_windows=3, min_shared=5):    # Stores 3 settings
        self.part, self.min_windows, self.min_shared = part, min_windows, min_shared

    def fit(self, train_feats):
        table = pd.DataFrame([f[self.part] for f in train_feats])       # Builds a table. One row per training window and one column per letter pair.
        table = table.loc[:, table.notna().sum() >= self.min_windows]   # When a window doesn’t contain a pair the cell is empty (NaN).
        self.median = table.median()
        mad = (table - self.median).abs().median()
        self.spread = (1.4826 * mad).clip(lower=5.0)                    # Median Absolute Deviation
        return self

    def score(self, f):
        x = f[self.part]
        common = x.index.intersection(self.median.index)
        if len(common) < self.min_shared:                               # Prevents an infinite loop from happening
            return float("inf")
        z = (x[common] - self.median[common]).abs() / self.spread[common]
        return float(z.mean())

# Outlier Count Detector
class OutlierCountDetector(ProfileDetector):

    def __init__(self, z_max=2.5, **kw):                                # Inherits from profile detector
        super().__init__(**kw)
        self.z_max = z_max

    def score(self, f):                                                 # Computes same z score then asks for each pair.
        x = f[self.part]
        common = x.index.intersection(self.median.index)
        if len(common) < self.min_shared:
            return 1.0
        z = (x[common] - self.median[common]).abs() / self.spread[common]
        return float((z > self.z_max).mean())
