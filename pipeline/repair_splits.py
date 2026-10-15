"""
One-off repair (safe to re-run): stored daily closes were updated incrementally, so a split that happened after a
company's first download left the older days un-adjusted (e.g. a 1-for-15 reverse split shows as a fake +1,400%
jump). Using the stored split list, find those jumps and re-base the days before them. prices.py now refetches the
whole history when it sees a split, so this only fixes the past.
"""
import csv, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
fixed = 0
for sp in (ROOT / "data" / "splits").glob("*.json"):
    splits = json.loads(sp.read_text())["splits"]
    p = ROOT / "data" / "prices" / f"{sp.stem}.csv"
    if not splits or not p.exists():
        continue
    rows = [[r["date"], float(r["close"])] for r in csv.DictReader(p.open()) if r["close"]]
    changed = False
    for day, num, den in splits:
        k = next((i for i, r in enumerate(rows) if r[0] >= day), None)
        if not k:
            continue
        jump, factor = rows[k][1] / rows[k - 1][1], den / num
        if 0.5 < jump / factor < 2 and (factor > 1.5 or factor < 0.67):      # an un-adjusted split day
            for r in rows[:k]:
                r[1] *= factor
            changed = True
            print(f"{sp.stem} split {day} {num}:{den} re-based {k} days (jump was x{jump:.1f})")
    if changed:
        fixed += 1
        with p.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "close"])
            for d, c in rows:
                w.writerow([d, f"{c:.6g}"])
print(f"repaired {fixed} companies")
