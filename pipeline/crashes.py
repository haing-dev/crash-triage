"""
Find every one-day fall of 50% or more (close vs previous close) in the downloaded prices.
Output: data/crashes.csv and reports/crashes.md

Filters (to avoid false crashes):
- previous close ≥ $1 (penny-stock noise excluded)
- the fall is not undone within 3 trading days (a +80% snap-back usually means a bad price print or split error)
"""
import csv, time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THRESH = -0.50
uni = {u["cik"]: u for u in csv.DictReader((ROOT / "data" / "universe.csv").open())}
out = []
for p in sorted((ROOT / "data" / "prices").glob("*.csv")):
    cik = p.stem
    rows = [(r["date"], float(r["close"])) for r in csv.DictReader(p.open()) if r["close"]]
    for i in range(1, len(rows)):
        (d0, c0), (d1, c1) = rows[i - 1], rows[i]
        if c0 <= 0 or c0 < 1:
            continue
        move = c1 / c0 - 1
        if move > THRESH:
            continue
        nxt = [c for _, c in rows[i + 1:i + 4]]
        if nxt and max(nxt) / c1 - 1 > 0.8:
            continue
        u = uni.get(cik, {})
        out.append({"cik": cik, "name": u.get("name", ""), "ticker": u.get("ticker", ""), "date": d1,
                    "month": time.strftime("%b %Y", time.strptime(d1, "%Y-%m-%d")),
                    "prev_close": f"{c0:.4g}", "close": f"{c1:.4g}", "move_pct": f"{move*100:.1f}",
                    "status": u.get("status", "")})
out.sort(key=lambda r: r["date"], reverse=True)
with open(ROOT / "data" / "crashes.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["cik"])
    w.writeheader()
    w.writerows(out)
yrs = Counter(r["date"][:4] for r in out)
rep = ["# Crashes (one-day fall of 50% or more)", f"scanned {len(list((ROOT/'data'/'prices').glob('*.csv')))} companies with prices", "",
       f"**{len(out)} crashes**", "", "| Year | Crashes |", "|---|---|"] + [f"| {y} | {n} |" for y, n in sorted(yrs.items())] + \
      ["", "Latest 15:", "", "| Date | Company | Ticker | Move |", "|---|---|---|---|"] + \
      [f"| {r['date']} | {r['name']} | {r['ticker']} | {r['move_pct']}% |" for r in out[:15]]
(ROOT / "reports" / "crashes.md").write_text("\n".join(rep) + "\n")
print("\n".join(rep))
