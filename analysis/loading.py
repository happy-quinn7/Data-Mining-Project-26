import re
from pathlib import Path

import numpy as np
import pandas as pd

MAX_PAUSE = 1000   # ms
MIN_HOLD, MAX_HOLD = 10, 500   # ms
SPACE = "␣"


# read in CSV, sort by the time - logical sort mechanism
def load_events(path):
    df = pd.read_csv(path, dtype={"participant_id": str, "session_id": str, "key": str},
                     keep_default_na=False)
    return df.sort_values("timestamp_ms", kind="stable").reset_index(drop=True)


# joins each down and up by press id and key -
# result: one row press with t_down and t_up
def pair_events(df):
    downs = df[df.event == "down"]
    ups = (df[df.event == "up"][["participant_id", "session_id", "press_id", "key_code", "timestamp_ms"]]
           .drop_duplicates(["participant_id", "session_id", "press_id"]))
    k = downs.merge(ups, on=["participant_id", "session_id", "press_id", "key_code"],
                    how="left", suffixes=("_down", "_up"))
    k = k.rename(columns={"participant_id": "participant", "session_id": "session",
                          "sample_id": "sample", "timestamp_ms_down": "t_down",
                          "timestamp_ms_up": "t_up"})
    k = k[["participant", "session", "sample", "press_id", "key_code", "key", "t_down", "t_up"]]
    return k.sort_values(["participant", "session", "t_down"]).reset_index(drop=True)


def is_text_key(key):
    return key.str.len().eq(1)          # "a", "é", ".", " " ; not "Shift", "Backspace"


# normal gap between consecutive press ids (always 1 with our collector)
def press_id_step(k):
    diffs = k.groupby(["participant", "session"]).press_id.diff()
    step = diffs[diffs > 0].groupby([k.participant, k.session]).agg(
        lambda s: s.mode().iloc[0] if len(s) else 1)
    return pd.MultiIndex.from_frame(k[["participant", "session"]]).map(step).fillna(1).to_numpy()

# gets all of the key timings we use in our scoring/ evaluation
def add_timings(k):
    k = k.copy()
    k["char"] = np.where(k.key == " ", SPACE, k.key.str.lower())
    k["text"] = is_text_key(k.key)

    k["hold"] = k.t_up - k.t_down # set to NaN if outside 10-500ms
    k.loc[~k.hold.between(MIN_HOLD, MAX_HOLD), "hold"] = np.nan

    g = k.groupby(["participant", "session", "sample"], sort=False)
    nxt_down = g.t_down.shift(-1)
    nxt_char = g.char.shift(-1)
    nxt_text = g.text.shift(-1, fill_value=False).astype(bool)

    k["latency"] = nxt_down - k.t_down
    k["flight"] = nxt_down - k.t_up
    k["pause"] = k.latency > MAX_PAUSE

    # next saved press id is not the expected next one
    k["lost_after"] = g.press_id.shift(-1).sub(k.press_id).ne(press_id_step(k)) \
        & g.press_id.shift(-1).notna()

    # digraph only counts if both keys are text keys (not shift/backspace),
    # latency is <= 1s and nothing lost between them
    clean = k.text & nxt_text & k.latency.le(MAX_PAUSE) & ~k.lost_after
    k["digraph"] = (k.char + nxt_char).where(clean)
    k.loc[~clean, ["latency", "flight"]] = np.nan
    return k


def load_session(path):
    return add_timings(pair_events(load_events(path)))


# loads all files with the naming convention - skips all others
def load_all(folder="../../data/processed"):
    paths = sorted(p for p in Path(folder).glob("*.csv")
                   if re.fullmatch(r"P\d+_S[123]\.csv", p.name))
    if not paths:
        raise FileNotFoundError(f"no P02_S1.csv-style files in {Path(folder).resolve()}")
    return pd.concat([load_session(p) for p in paths], ignore_index=True)


# per session counts and the share of missing holds/ lost presses
def quality_report(k):
    return k.groupby(["participant", "session"]).agg(
        presses=("key", "size"),
        text_keys=("text", "sum"),
        backspaces=("key", lambda s: (s == "Backspace").sum()),
        hold_missing=("hold", lambda s: s.isna().mean()),
        lost_presses=("lost_after", "mean"),
        digraphs=("digraph", "count"),
        pauses=("pause", "sum"),
        minutes=("t_down", lambda s: (s.max() - s.min()) / 60000),
    ).round(3)