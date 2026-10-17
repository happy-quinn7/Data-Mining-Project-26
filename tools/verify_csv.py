"""
Health check for collector CSVs.  Usage: python verify_csv.py P01_S1.csv [P01_S2.csv ...]

Run it on a short test export (about 300 characters) BEFORE a real session.
A healthy collector gives PASS on every check:
  1. same number of `down` and `up` rows
  2. press_ids are contiguous: highest - lowest + 1 == number of downs
     (a bigger range means events were lost; ids may start above 1 if you did not Clear)
  3. every press_id has exactly one down and one up
  4. no key_code mismatch between a press and its release
  5. hold times are positive; timestamps never go backwards
Exit code is 0 when all files pass, 1 otherwise.
"""
import sys

import pandas as pd

COLUMNS = ["participant_id", "session_id", "sample_id", "event",
           "key_code", "timestamp_ms", "press_id", "key"]


def check(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing_cols = [c for c in COLUMNS if c not in df.columns]
    if missing_cols:
        print(f"{path}: FAIL - missing columns {missing_cols}")
        return False
    df["timestamp_ms"] = pd.to_numeric(df["timestamp_ms"], errors="coerce")
    df["press_id"] = pd.to_numeric(df["press_id"], errors="coerce")
    df = df.dropna(subset=["timestamp_ms", "press_id"])

    downs = df[df.event == "down"]
    ups = df[df.event == "up"]
    n_down, n_up = len(downs), len(ups)
    max_id = int(df.press_id.max()) if len(df) else 0
    span = max_id - int(df.press_id.min()) + 1 if len(df) else 0
    d_ids, u_ids = set(downs.press_id), set(ups.press_id)

    paired = downs.merge(ups, on="press_id", suffixes=("_d", "_u"))
    hold = paired.timestamp_ms_u - paired.timestamp_ms_d

    results = [
        ("down rows == up rows", n_down == n_up, f"{n_down} down, {n_up} up"),
        ("press_ids contiguous (no lost events)", span == n_down,
         f"press_id range spans {span}, downs {n_down}"
         + (f"  -> about {span - n_down} events were never saved" if span > n_down else "")),
        ("every press has its release", d_ids == u_ids,
         f"{len(d_ids - u_ids)} downs without up, {len(u_ids - d_ids)} ups without down"),
        ("release matches its press key", bool((paired.key_code_d == paired.key_code_u).all()),
         f"{int((paired.key_code_d != paired.key_code_u).sum())} mismatches"),
        ("hold times positive", bool((hold > 0).all()) and len(hold) > 0,
         f"{int((hold <= 0).sum())} non-positive; median {hold.median():.1f} ms" if len(hold) else "no pairs"),
        ("timestamps in order", bool(df.timestamp_ms.is_monotonic_increasing),
         f"{int((df.timestamp_ms.diff() < 0).sum())} backwards steps"),
    ]
    ok = all(r[1] for r in results)
    print(f"{path}: {'PASS' if ok else 'FAIL'}  ({len(df)} rows)")
    for name, passed, detail in results:
        print(f"  [{'ok' if passed else 'XX'}] {name}: {detail}")
    return ok


if __name__ == "__main__":
    files = sys.argv[1:] or ["P07_S1.csv"]
    all_ok = all([check(f) for f in files])   # list: check every file, no short-circuit
    if not all_ok:
        print("\nFAIL: reload the extension (chrome://extensions), refresh the chat tab, "
              "clear data, and test again.")
    sys.exit(0 if all_ok else 1)
