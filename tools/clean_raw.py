"""Repair raw collector CSVs. Output files keep the exact class format and the
same names (P02_S1.csv, P02_S2.csv, P02_S3.csv), only in another folder.

Usage:
    python tools/clean_raw.py data/raw data/processed          # every file in the folder
    python tools/clean_raw.py data/raw/P02_S1.csv data/processed   # one file

File names must be <participant>_<session>.csv, e.g. P02_S1.csv, with the
session S1, S2 or S3. Anything else is refused, so a misnamed file can never
end up in the analysis.

What it fixes (and why):
1. participant_id and session_id are set from the file name (P02_S1.csv ->
   P02, S1), so typos like "Po2" (letter o) don't create a fake participant.
2. Several recordings in one file: press_id restarts at 1 when the collector is
   reset, which makes press_ids ambiguous. A new recording starts when press_id
   drops back or after a gap of more than 30 minutes. The longest recording is
   kept as the session; the others are listed in the report and dropped.
3. Exact duplicate rows are dropped.
4. Releases with no matching press (or with a different key_code) are dropped:
   no hold time can be computed from them.
5. Presses with no matching release are KEPT: their press time is still valid
   for latencies. The loader leaves their hold time empty.
"""
import re
import sys
from pathlib import Path

import pandas as pd

COLUMNS = ["participant_id", "session_id", "sample_id", "event",
           "key_code", "timestamp_ms", "press_id", "key"]
NAME = re.compile(r"^(P\d+)_(S[123])\.csv$")
GAP_MS = 30 * 60 * 1000
MIN_KEYSTROKES = 2000


def parse_name(path):
    m = NAME.match(Path(path).name)
    if not m:
        raise ValueError(f"{Path(path).name}: expected a name like P02_S1.csv (session S1, S2 or S3)")
    return m.group(1), m.group(2)


def split_recordings(df):
    """Number each recording: a new one starts when press_id resets or after a long gap."""
    downs = df.event.eq("down")
    last_id = df.press_id.where(downs).ffill().shift().fillna(0)
    reset = downs & (df.press_id < last_id - 5)
    gap = df.timestamp_ms.diff().fillna(0) > GAP_MS
    return (reset | gap).cumsum()


def clean(path_in):
    pid, sid = parse_name(path_in)
    report = []
    df = pd.read_csv(path_in, keep_default_na=False)
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"{Path(path_in).name}: missing columns {sorted(missing)}")
    n0 = len(df)
    df = df.sort_values("timestamp_ms", kind="stable").reset_index(drop=True)

    # 1. pseudonym and session from the file name
    for col, value in (("participant_id", pid), ("session_id", sid)):
        bad = sorted(set(df[col].astype(str)) - {value})
        if bad:
            report.append(f"{col}: {bad} renamed to {value}")
        df[col] = value

    # 2. separate recordings
    rec = split_recordings(df)
    sizes = rec.value_counts().sort_index()
    keep = sizes.idxmax()
    if len(sizes) > 1:
        for r, n in sizes.items():
            start = pd.to_datetime(df.timestamp_ms[rec == r].min(), unit="ms")
            report.append(f"recording {r}: {n} rows from {start:%Y-%m-%d %H:%M}"
                          + ("  <- kept" if r == keep else "  <- dropped"))
    df = df[rec == keep]

    # 3. duplicates
    dup = df.duplicated(subset=["event", "press_id", "key_code", "timestamp_ms"])
    df = df[~dup]

    # 4. releases without a matching press
    code_of = df[df.event == "down"].drop_duplicates("press_id").set_index("press_id").key_code
    orphan_up = (df.event == "up") & ~df.press_id.map(code_of).eq(df.key_code)
    df = df[~orphan_up]

    # 5. presses never released (kept)
    released = set(df[df.event == "up"].press_id)
    downs = df.event == "down"
    orphan_down = downs & ~df.press_id.isin(released)

    n_down = int(downs.sum())
    report += [
        f"duplicate rows dropped: {int(dup.sum())}",
        f"releases without a matching press dropped: {int(orphan_up.sum())}",
        f"presses without a release kept: {int(orphan_down.sum())} ({orphan_down.sum() / max(n_down, 1):.1%})",
        f"rows: {n0} -> {len(df)}   keystrokes: {n_down}"
        + ("" if n_down >= MIN_KEYSTROKES else f"   WARNING: fewer than {MIN_KEYSTROKES}"),
    ]
    return df[COLUMNS], report


def main(src, dst):
    src, dst = Path(src), Path(dst)
    files = sorted(src.glob("*.csv")) if src.is_dir() else [src]
    dst.mkdir(parents=True, exist_ok=True)
    for f in files:
        try:
            df, report = clean(f)
        except ValueError as e:
            print(f"SKIPPED {e}")
            continue
        df.to_csv(dst / f.name, index=False)
        print(f"{f.name}:")
        print("\n".join("  " + line for line in report))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])