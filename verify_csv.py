"""Quick sanity check for a collector CSV. Usage: python verify_csv.py P07_S1.csv"""
import sys
import pandas as pd

path = sys.argv[1] if len(sys.argv) > 1 else "P07_S1.csv"
df = pd.read_csv(path)

downs = df[df.event == "down"]
ups = df[df.event == "up"]
print(f"rows: {len(df)}  (down={len(downs)}, up={len(ups)})")

# every down should have exactly one up with the same press_id
missing = set(downs.press_id) - set(ups.press_id)
extra = set(ups.press_id) - set(downs.press_id)
if missing:
    print(f"WARNING: {len(missing)} presses with no release")
if extra:
    print(f"WARNING: {len(extra)} releases with no press")
if not missing and not extra:
    print("OK: every press has a matching release")

# hold time = up_time - down_time, per press_id, must be positive
t = df.pivot_table(index="press_id", columns="event", values="timestamp_ms")
hold = (t["up"] - t["down"]).dropna()
print("hold times (ms): min=%.1f median=%.1f max=%.1f"
      % (hold.min(), hold.median(), hold.max()))
if (hold <= 0).any():
    print(f"WARNING: {(hold <= 0).sum()} non-positive hold times")
