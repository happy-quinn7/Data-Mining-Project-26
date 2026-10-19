import numpy as np
import pandas as pd

from features import all_features


def split_features(feats, train_sessions=("S1", "S2"), test_sessions=("S3",),
                   split="sessions", train_frac=0.6):
    out = {}
    for pid in sorted({f["participant"] for f in feats}):
        mine = [f for f in feats if f["participant"] == pid]
        if split == "sessions":
            train = [f for f in mine if f["session"] in train_sessions]
            test = [f for f in mine if f["session"] in test_sessions]
        else:   # "within": time-ordered split inside each session
            train, test = [], []
            for sid in sorted({f["session"] for f in mine}):
                s = sorted((f for f in mine if f["session"] == sid), key=lambda f: f["index"])
                cut = int(round(len(s) * train_frac))
                train += s[:cut]
                test += s[cut:]
        if train and test:
            out[pid] = (train, test)
    return out


def score_all(feats, make_detector, **split_kw):
    splits = split_features(feats, **split_kw)
    rows = []
    for pid, (train, _) in splits.items():
        det = make_detector().fit(train)
        for other, (_, test) in splits.items():
            for f in test:
                rows.append({"claimed": pid, "true": other, "session": f["session"],
                             "index": f["index"], "genuine": other == pid,
                             "score": det.score(f)})
    return pd.DataFrame(rows)


def far_frr(scores, thresholds=None):
    g = scores.loc[scores.genuine, "score"].to_numpy()
    i = scores.loc[~scores.genuine, "score"].to_numpy()
    if thresholds is None:
        finite = np.concatenate([g, i])
        finite = finite[np.isfinite(finite)]
        thresholds = np.unique(np.concatenate([[-np.inf], finite, [np.inf]]))
    frr = np.array([(g > t).mean() for t in thresholds])
    far = np.array([(i <= t).mean() for t in thresholds])
    return pd.DataFrame({"threshold": thresholds, "FAR": far, "FRR": frr})


def eer(curve):
    j = (curve.FAR - curve.FRR).abs().idxmin()
    return float((curve.FAR[j] + curve.FRR[j]) / 2), float(curve.threshold[j])


def frr_at_far(curve, max_far=0.01):
    ok = curve[curve.FAR <= max_far]
    return float(ok.FRR.min()) if len(ok) else 1.0


def per_user(scores):
    return pd.Series({u: eer(far_frr(s))[0] for u, s in scores.groupby("claimed")}, name="EER")


def compare(feats, detectors, splits):
    """One row per (split, detector): EER and FRR at 1% FAR.

    detectors: {name: make_detector}; splits: {name: split_features keywords}.
    A split that leaves no genuine or no impostor tests gets NaN results.
    """
    rows = []
    for split_name, split_kw in splits.items():
        for det_name, make in detectors.items():
            scores = score_all(feats, make, **split_kw)
            row = {"split": split_name, "detector": det_name,
                   "users": scores.claimed.nunique() if len(scores) else 0,
                   "EER": np.nan, "FRR@FAR1%": np.nan,
                   "genuine_tests": int(scores.genuine.sum()) if len(scores) else 0,
                   "impostor_tests": int((~scores.genuine).sum()) if len(scores) else 0}
            if row["genuine_tests"] and row["impostor_tests"]:
                curve = far_frr(scores)
                row["EER"] = eer(curve)[0]
                row["FRR@FAR1%"] = frr_at_far(curve, 0.01)
            rows.append(row)
    return pd.DataFrame(rows)


def window_length_study(k, make_detector, lengths=(50, 100, 200, 300, 500),
                        min_count=2, **split_kw):
    rows = []
    for n in lengths:
        feats = all_features(k, n=n, min_count=min_count)
        scores = score_all(feats, make_detector, **split_kw)
        if scores.empty or scores.genuine.all() or (~scores.genuine).all():
            continue
        curve = far_frr(scores)
        rows.append({"window": n, "EER": eer(curve)[0],
                     "FRR@FAR1%": frr_at_far(curve, 0.01),
                     "not_comparable": float(np.isinf(scores.score).mean()),
                     "genuine_tests": int(scores.genuine.sum()),
                     "impostor_tests": int((~scores.genuine).sum())})
    return pd.DataFrame(rows)