#!/usr/bin/env python3
"""
clean_keystrokes.py - clean and collate keystroke-collector CSVs for ML.

Input : one or more raw session files in the class format
        participant_id,session_id,sample_id,event,key_code,timestamp_ms,press_id,key
        (named like P01_S1.csv).  Pass files, folders, or nothing (= this folder).
Output: (written to ./cleaned by default, all plain UTF-8 CSV, read with pd.read_csv)
  presses_clean.csv  ONE ROW PER KEY PRESS, all sessions collated. ML-ready:
                     hold_ms plus digraph latencies dd/ud/uu/du to the next key.
  events_clean.csv   the same data back in the raw one-row-per-event class format,
                     complete presses only (use this for pooling with the class).
  cleaning_report.csv  what was dropped, per file.

What "clean" means here (every threshold is a command-line option):
  1. malformed rows, exact duplicates, duplicate down/up for one press -> dropped
  2. orphans: a down with no up, or an up with no down -> dropped
  3. key_code differs between a press and its release -> dropped
  4. hold <= 0 ms or > --max-hold-ms (stuck key) -> dropped
  5. samples with < --min-keys-per-sample presses -> dropped; sample_id renumbered
     contiguously (original kept in sample_id_raw), plus a contiguous `seq`
  6. long pauses (> --max-gap-ms between two presses, e.g. reading the answer):
     rows are KEPT but start a new segment_id, and the latency features that would
     span the pause are set to NaN, so pauses never pollute digraph statistics.
  7. a jump in press_id between two kept presses means a key is missing in between
     (lost or dropped above): same treatment, new segment, no latency across it.
  Backspace/Delete and modifiers are kept and flagged (is_correction, is_modifier).

Privacy: key and key_code contain what you typed. Keep these files within your pair.

Usage:  python clean_keystrokes.py                # everything in this folder
        python clean_keystrokes.py data/ --out out --max-gap-ms 3000
"""
import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAW_COLUMNS = ["participant_id", "session_id", "sample_id", "event",
               "key_code", "timestamp_ms", "press_id", "key"]
FILE_RE = re.compile(r"^(P\d+)_(S\d+)\.csv$", re.IGNORECASE)
MODIFIERS = {"ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight",
             "AltLeft", "AltRight", "MetaLeft", "MetaRight", "CapsLock"}
CORRECTIONS = {"Backspace", "Delete"}
MIN_KEYS_PROTOCOL = 2000  # subject 3.2: at least 2,000 keystrokes per session


def read_raw(path):
    # Read everything as text first so a stray value cannot change a column's dtype.
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in RAW_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}")
    return df[RAW_COLUMNS].copy()


def clean_file(path, a):
    st = {"file": path.name}
    df = read_raw(path)
    st["raw_rows"] = len(df)

    for c in ("sample_id", "press_id", "timestamp_ms"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["event"] = df["event"].str.strip().str.lower()
    bad = (df[["sample_id", "press_id", "timestamp_ms"]].isna().any(axis=1)
           | ~df["event"].isin(["down", "up"]) | (df["key_code"] == ""))
    st["malformed_rows"] = int(bad.sum())
    df = df[~bad].astype({"sample_id": "int64", "press_id": "int64"})

    n = len(df)
    df = df.drop_duplicates()
    df = df.sort_values("timestamp_ms", kind="stable")
    df = df.drop_duplicates(["press_id", "event"], keep="first")
    st["duplicate_rows"] = n - len(df)

    downs = df[df.event == "down"].rename(columns={"timestamp_ms": "press_ms"})
    ups = df[df.event == "up"][["press_id", "key_code", "timestamp_ms"]].rename(
        columns={"key_code": "key_code_up", "timestamp_ms": "release_ms"})
    st["orphan_up"] = int((~ups.press_id.isin(downs.press_id)).sum())

    p = downs.merge(ups, on="press_id", how="left")
    st["orphan_down"] = int(p.release_ms.isna().sum())
    p = p.dropna(subset=["release_ms"])

    mismatch = p.key_code != p.key_code_up
    st["keycode_mismatch"] = int(mismatch.sum())
    p = p[~mismatch].copy()

    p["hold_ms"] = p.release_ms - p.press_ms
    invalid = (p.hold_ms <= 0) | (p.hold_ms > a.max_hold_ms)
    st["bad_hold"] = int(invalid.sum())
    p = p[~invalid]

    size = p.groupby("sample_id")["press_id"].transform("size")
    small = size < a.min_keys_per_sample
    st["tiny_sample_rows"] = int(small.sum())
    p = p[~small]

    if p.empty:
        st.update(clean_presses=0, segments=0, long_pauses=0)
        return p, st

    p = p.sort_values(["sample_id", "press_ms"], kind="stable").reset_index(drop=True)
    p["sample_id_raw"] = p["sample_id"]
    p["sample_id"] = p["sample_id"].rank(method="dense").astype(int) - 1
    p["seq"] = np.arange(len(p))
    p["t_ms"] = p.press_ms - p.press_ms.min()

    # pauses -> segments (never across samples)
    g = p.groupby("sample_id")
    # press_id counts every key the collector saw, so a jump means a key is missing
    # between two kept presses (lost event or dropped above): treat it like a break.
    first = g.press_id.shift(1).isna()
    missing_key = ~first & ((p.press_id - g.press_id.shift(1)) != 1)
    p["gap_before_ms"] = (p.press_ms - g.press_ms.shift(1)).where(~missing_key)
    long_pause = p.gap_before_ms > a.max_gap_ms
    new_seg = first | missing_key | long_pause
    p["segment_id"] = new_seg.cumsum() - 1
    st["long_pauses"] = int(long_pause.sum())
    st["missing_key_breaks"] = int(missing_key.sum())
    st["segments"] = int(p.segment_id.nunique())

    # digraph features to the next key, only inside a segment
    g = p.groupby("sample_id")
    nxt_press, nxt_rel = g.press_ms.shift(-1), g.release_ms.shift(-1)
    same_seg = p.segment_id == g.segment_id.shift(-1)
    p["next_key_code"] = g.key_code.shift(-1).where(same_seg)
    p["next_key"] = g.key.shift(-1).where(same_seg)
    p["dd_ms"] = (nxt_press - p.press_ms).where(same_seg)    # press-press
    p["ud_ms"] = (nxt_press - p.release_ms).where(same_seg)  # release-press (flight)
    p["uu_ms"] = (nxt_rel - p.release_ms).where(same_seg)    # release-release
    p["du_ms"] = (nxt_rel - p.press_ms).where(same_seg)      # press-release

    p["is_modifier"] = p.key_code.isin(MODIFIERS)
    p["is_correction"] = p.key_code.isin(CORRECTIONS)
    st["clean_presses"] = len(p)
    return p, st


PRESS_COLUMNS = ["participant_id", "session_id", "sample_id", "segment_id", "seq",
                 "press_id", "sample_id_raw", "key_code", "key", "press_ms",
                 "release_ms", "t_ms", "hold_ms", "gap_before_ms", "next_key_code",
                 "next_key", "dd_ms", "ud_ms", "uu_ms", "du_ms",
                 "is_modifier", "is_correction"]


def to_events(p):
    """Rebuild the raw class format (one row per event) from complete presses."""
    base = p[["participant_id", "session_id", "key_code", "press_id", "key"]].copy()
    base["sample_id"] = p["sample_id_raw"]
    d = base.assign(event="down", timestamp_ms=p.press_ms)
    u = base.assign(event="up", timestamp_ms=p.release_ms)
    ev = pd.concat([d, u]).sort_values(["participant_id", "session_id", "timestamp_ms"],
                                       kind="stable")
    ev["timestamp_ms"] = ev["timestamp_ms"].round(3)
    return ev[RAW_COLUMNS]


def find_inputs(paths):
    files = []
    for raw in paths:
        pth = Path(raw)
        if pth.is_dir():
            files += sorted(f for f in pth.iterdir() if FILE_RE.match(f.name))
        elif pth.is_file():
            files.append(pth)
        else:
            sys.exit(f"not found: {pth}")
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="CSV files or folders (default: this folder)")
    ap.add_argument("--out", default="cleaned", help="output folder (default: ./cleaned)")
    ap.add_argument("--max-hold-ms", type=float, default=1000,
                    help="drop presses held longer than this (default 1000)")
    ap.add_argument("--max-gap-ms", type=float, default=2000,
                    help="press-to-press gap that starts a new segment (default 2000)")
    ap.add_argument("--min-keys-per-sample", type=int, default=5,
                    help="drop samples with fewer presses (default 5)")
    a = ap.parse_args()

    files = find_inputs(a.paths or [Path(__file__).resolve().parent])
    if not files:
        sys.exit("no P##_S##.csv files found")

    parts, stats = [], []
    for f in files:
        p, st = clean_file(f, a)
        parts.append(p)
        stats.append(st)
    presses = pd.concat(parts, ignore_index=True)
    if presses.empty:
        sys.exit("nothing left after cleaning")

    presses = presses.sort_values(["participant_id", "session_id", "press_ms"],
                                  kind="stable").reset_index(drop=True)
    for c in ("press_ms", "release_ms", "t_ms", "hold_ms", "gap_before_ms",
              "dd_ms", "ud_ms", "uu_ms", "du_ms"):
        presses[c] = presses[c].round(3)
    presses = presses[PRESS_COLUMNS]

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    presses.to_csv(out / "presses_clean.csv", index=False, encoding="utf-8", lineterminator="\n")
    to_events(presses.assign(press_ms=presses.press_ms, release_ms=presses.release_ms)).to_csv(
        out / "events_clean.csv", index=False, encoding="utf-8", lineterminator="\n")
    report = pd.DataFrame(stats).fillna(0)
    report.to_csv(out / "cleaning_report.csv", index=False, encoding="utf-8", lineterminator="\n")

    # sanity check: files really read back with plain pandas, text keys intact
    back = pd.read_csv(out / "presses_clean.csv")
    assert len(back) == len(presses) and (back["key"] == presses["key"]).all()
    assert not back[["press_ms", "release_ms", "hold_ms"]].isna().any().any()

    print(report.to_string(index=False))
    print(f"\n{len(presses)} clean presses -> {out}/presses_clean.csv "
          f"({presses.groupby(['participant_id', 'session_id']).size().to_dict()})")
    for (pid, sid), n in presses.groupby(["participant_id", "session_id"]).size().items():
        if n < MIN_KEYS_PROTOCOL:
            print(f"WARNING: {pid}_{sid} has {n} clean keystrokes "
                  f"(protocol asks for >= {MIN_KEYS_PROTOCOL})")


if __name__ == "__main__":
    main()
