# How to run:
#   python analysis/run.py                 # clean the raw data, then evaluate
#   python analysis/run.py --no-clean      # skip cleaning, use processed data as is
#   python analysis/run.py --window 300    # key presses per window (default 200)

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import clean_raw
from detector import KNNDetector, OutlierCountDetector, ProfileDetector
from evaluation import compare, far_frr, per_user, score_all, window_length_study
from features import all_features
from identify import identification_accuracy
from loading import load_all, quality_report

RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

# The three detectors that are compared
DETECTORS = {
    "KNN (k=3)": lambda: KNNDetector(k=3),
    "Profile": ProfileDetector,
    "Outlier count": OutlierCountDetector,
}


# Pick the test splits our sessions allow 
def splits_for(k):
    sessions = k.groupby("participant").session.unique().map(set)
    splits = {}
    if (sessions.map(lambda s: {"S1", "S2", "S3"} <= s)).sum() >= 2:
        splits["S1+S2 -> S3"] = dict(train_sessions=("S1", "S2"), test_sessions=("S3",))
    if (sessions.map(lambda s: {"S1", "S2"} <= s)).sum() >= 2:
        splits["S1 -> S2"] = dict(train_sessions=("S1",), test_sessions=("S2",))
    splits["within session"] = dict(split="within")
    return splits


# Print an underlined section title
def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def main():
    ap = argparse.ArgumentParser(description="From raw sessions to results in one command.")
    ap.add_argument("--no-clean", action="store_true", help="skip tools/clean_raw.py")
    ap.add_argument("--window", type=int, default=200, help="keystrokes per window")
    args = ap.parse_args()
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 20)
    warnings.filterwarnings("ignore", category=RuntimeWarning)   # hide warnings from empty windows

    # Clean the raw data
    if not args.no_clean:
        heading("1. Cleaning data/raw -> data/processed")
        clean_raw.main(RAW, PROCESSED)

    # Load it and show data quality
    k = load_all(PROCESSED)
    heading("2. Data quality per session")
    print(quality_report(k).to_string())
    sessions = k.groupby("participant").session.unique().map(sorted)
    print("\nSessions per participant:", ", ".join(f"{p} {'/'.join(s)}" for p, s in sessions.items()))

    # Test every detector on every split
    feats = all_features(k, n=args.window, min_count=2)
    splits = splits_for(k)
    heading(f"3. Detectors ({args.window}-keystroke windows; lower EER is better)")
    results = compare(feats, DETECTORS, splits)
    shown = results.assign(**{c: results[c].map("{:.1%}".format) for c in ("EER", "FRR@FAR1%")})
    print(shown.to_string(index=False))

    # Take the best detector on the main split and show its error per person
    main_split = next(iter(splits))
    best = results[results.split == main_split].sort_values("EER").iloc[0]
    heading(f"4. EER per participant ({best.detector}, {main_split})")
    scores = score_all(feats, DETECTORS[best.detector], **splits[main_split])
    print(per_user(scores).map("{:.1%}".format).to_string())

    # Try different window lengths with the best detector
    heading(f"5. Window length ({best.detector}, {main_split})")
    study = window_length_study(k, DETECTORS[best.detector], **splits[main_split])
    for c in ("EER", "FRR@FAR1%", "not_comparable"):
        study[c] = study[c].map("{:.1%}".format)
    print(study.to_string(index=False))
    print("\nnot_comparable = share of tests where the two windows shared too few digraphs to compare.")

    # Guess who is typing, for a few window lengths
    heading("6. Identification: who is typing? (KNN, closest participant wins)")
    for name, kw in splits.items():
        if "train_sessions" not in kw:
            continue  # only splits that test on a new day
        for n in (100, 200, 400):
            acc, tests = identification_accuracy(all_features(k, n=n, min_count=2), **kw)
            if tests:
                print(f"{name:12s} {n:>3}-keystroke windows: right {acc:.0%} of {tests} windows")
    print("This is what the website's \"Who's typing?\" mode does live.")


if __name__ == "__main__":
    main()
