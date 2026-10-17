import numpy as np
import pandas as pd

from similarity import shared_relative


class KNNDetector:

    def __init__(self, distance=shared_relative, k=3):
        self.distance, self.k = distance, k

    def fit(self, train_feats):
        if not train_feats:
            raise ValueError("no training windows")
        self.train = list(train_feats)
        return self

    def score(self, f):
        d = np.sort([self.distance(f, t) for t in self.train])
        return float(np.mean(d[: min(self.k, len(d))]))


class ProfileDetector:

    def __init__(self, part="digraph", min_windows=3, min_shared=5):
        self.part, self.min_windows, self.min_shared = part, min_windows, min_shared

    def fit(self, train_feats):
        table = pd.DataFrame([f[self.part] for f in train_feats])
        table = table.loc[:, table.notna().sum() >= self.min_windows]
        self.median = table.median()
        mad = (table - self.median).abs().median()
        self.spread = (1.4826 * mad).clip(lower=5.0)
        return self

    def score(self, f):
        x = f[self.part]
        common = x.index.intersection(self.median.index)
        if len(common) < self.min_shared:
            return float("inf")
        z = (x[common] - self.median[common]).abs() / self.spread[common]
        return float(z.mean())


class OutlierCountDetector(ProfileDetector):

    def __init__(self, z_max=2.5, **kw):
        super().__init__(**kw)
        self.z_max = z_max

    def score(self, f):
        x = f[self.part]
        common = x.index.intersection(self.median.index)
        if len(common) < self.min_shared:
            return 1.0
        z = (x[common] - self.median[common]).abs() / self.spread[common]
        return float((z > self.z_max).mean())