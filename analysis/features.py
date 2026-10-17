import pandas as pd


def make_windows(k, n=200, step=None):
    step = step or n
    windows = []
    for (pid, sid), g in k.groupby(["participant", "session"], sort=True):
        g = g.sort_values("t_down")
        for i, start in enumerate(range(0, len(g) - n + 1, step)):
            windows.append({"participant": pid, "session": sid, "index": i,
                            "t_start": g.t_down.iloc[start], "keys": g.iloc[start:start + n]})
    return windows


def features(window, min_count=3):
    keys = window["keys"]

    d = keys.groupby("digraph").latency.agg(["median", "count"])
    h = keys[keys.text].groupby("char").hold.agg(["median", "count"])

    minutes = (keys.t_down.iloc[-1] - keys.t_down.iloc[0]) / 60000
    glob = pd.Series({
        "latency_median": keys.latency.median(),
        "latency_iqr": keys.latency.quantile(.75) - keys.latency.quantile(.25),
        "hold_median": keys.hold.median(),
        "hold_iqr": keys.hold.quantile(.75) - keys.hold.quantile(.25),
        "flight_median": keys.flight.median(),
        "overlap_rate": (keys.flight < 0).mean(),          # next key pressed before release
        "backspace_rate": (keys.key == "Backspace").mean(),
        "pause_rate": keys.pause.mean(),
        "keys_per_min": len(keys) / minutes if minutes > 0 else float("nan"),
    })
    return {
        "participant": window["participant"], "session": window["session"],
        "index": window["index"],
        "digraph": d.loc[d["count"] >= min_count, "median"],
        "hold": h.loc[h["count"] >= min_count, "median"],
        "global": glob,
    }


def all_features(k, n=200, step=None, min_count=3):
    return [features(w, min_count) for w in make_windows(k, n, step)]


def global_matrix(feats):
    rows = pd.DataFrame([f["global"] for f in feats])
    meta = pd.DataFrame([{k: f[k] for k in ("participant", "session", "index")} for f in feats])
    return pd.concat([meta, rows], axis=1)