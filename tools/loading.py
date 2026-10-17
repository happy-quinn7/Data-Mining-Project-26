"""From raw events (one row per press or release) to keystrokes (one row per press).

Main entry point: load_all(folder) -> one DataFrame with, for every press:
    participant, session, sample, key_code, key, t_down, t_up,
    hold      press -> release of this key (NaN if the release was lost)
    latency   press of this key -> press of the next key  (down-down)
    flight    release of this key -> press of the next key (up-down, < 0 = overlap)
    digraph   e.g. "le", "e␣" (NaN when the pair is not a clean typing pair)

Cleaning choices (justify them in the report):
- The character is taken from the PRESS row: on release, Shift may already be
  up, so the release of "T" can be logged as "t".
- Letters are lowercased: we model rhythm, not capitalisation.
- Digraphs only between two text keys (letters, digits, punctuation, space).
  Shift, Backspace, Enter, arrows... break the chain: a latency that spans
  a correction or a modifier is not ordinary typing rhythm.
- Digraphs never span two samples (messages) nor a pause > MAX_PAUSE ms
  (the user is reading the answer or thinking).
- Digraphs never span a press the collector lost (detected by a jump in
  press_id), otherwise "the" with a lost "h" would count as a slow "te".
- Holds outside [MIN_HOLD, MAX_HOLD] are set to NaN (lost release, key held
  down on purpose, or a timing glitch).
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd

MAX_PAUSE = 1000   # ms
MIN_HOLD, MAX_HOLD = 10, 500   # ms
SPACE = "␣"


def load_events(path):
    df = pd.read_csv(path, dtype={"participant_id": str, "session_id": str, "key": str},
                     keep_default_na=False)
    return df.sort_values("timestamp_ms", kind="stable").reset_index(drop=True)


def pair_events(df):
    """One row per press, with its release time when the release was recorded."""
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


def press_id_step(k):
    """Usual press_id increment between consecutive presses, per session
    (1 for most collectors; computed so other numbering schemes still work)."""
    diffs = k.groupby(["participant", "session"]).press_id.diff()
    step = diffs[diffs > 0].groupby([k.participant, k.session]).agg(
        lambda s: s.mode().iloc[0] if len(s) else 1)
    return pd.MultiIndex.from_frame(k[["participant", "session"]]).map(step).fillna(1).to_numpy()


def add_timings(k):
    k = k.copy()
    k["char"] = np.where(k.key == " ", SPACE, k.key.str.lower())
    k["text"] = is_text_key(k.key)

    k["hold"] = k.t_up - k.t_down
    k.loc[~k.hold.between(MIN_HOLD, MAX_HOLD), "hold"] = np.nan

    g = k.groupby(["participant", "session", "sample"], sort=False)
    nxt_down = g.t_down.shift(-1)
    nxt_char = g.char.shift(-1)
    nxt_text = g.text.shift(-1, fill_value=False).astype(bool)

    k["latency"] = nxt_down - k.t_down
    k["flight"] = nxt_down - k.t_up
    k["pause"] = k.latency > MAX_PAUSE

    # A press lost by the collector between two recorded presses would create a
    # fake digraph ("the" with "h" lost -> "te" with a double latency). press_id
    # counts every press, so a jump larger than the usual step reveals the loss.
    k["lost_after"] = g.press_id.shift(-1).sub(k.press_id).ne(press_id_step(k)) \
        & g.press_id.shift(-1).notna()

    clean = k.text & nxt_text & k.latency.le(MAX_PAUSE) & ~k.lost_after
    k["digraph"] = (k.char + nxt_char).where(clean)
    k.loc[~clean, ["latency", "flight"]] = np.nan
    return k


def load_session(path):
    return add_timings(pair_events(load_events(path)))


def load_all(folder="../../data/processed"):
    """Every P<nn>_S<1-3>.csv in the folder, as one keystroke table."""
    paths = sorted(p for p in Path(folder).glob("*.csv")
                   if re.fullmatch(r"P\d+_S[123]\.csv", p.name))
    if not paths:
        raise FileNotFoundError(f"no P02_S1.csv-style files in {Path(folder).resolve()}")
    return pd.concat([load_session(p) for p in paths], ignore_index=True)


def quality_report(k):
    """Per session: how much of the data is usable."""
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