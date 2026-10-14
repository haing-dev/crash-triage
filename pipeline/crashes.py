"""
Find biotech crashes in the downloaded daily closes.

A crash = the stock falls 50% or more within 5 trading days, AND the biggest single-day fall inside that window is
at least 25% (the "news day"). The news day is the crash date: it is used to find the company's announcement and to
start the 365-day clock. Gradual slides with no news day are left out.

Filters against false crashes:
- the close before the window is at least $1 (penny-stock noise excluded)
- the fall is not undone within 3 trading days (a +80% snap-back usually means a bad price print or split error)
- one crash per company per 20 trading days (further falls right after count as the same crash)

Output: data/crashes.csv and reports/crashes.md
"""
import csv, time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FALL, WINDOW, NEWS_DAY, COOLDOWN = -0.50, 5, -0.25, 20
uni = {u["cik"]: u for u in csv.DictReader((ROOT / "data" / "universe.csv").open())}
out = []
for p in sorted((ROOT / "data" / "prices").glob("*.csv")):
    cik = p.stem
    if cik == "XBI":
        continue
    rows = [(r["date"], float(r["close"])) for r in csv.DictReader(p.open()) if r["close"]]
    n, j = len(rows), 1
    while j < n:
        # peak = highest close in the 5 trading days before day j; crash if day j is 50%+ below it
        k0 = max(0, j - WINDOW)
        start = max(range(k0, j), key=lambda k: rows[k][1])
        base = rows[start][1]
        if base < 1 or rows[j][1] / base - 1 > FALL:
            j += 1
            continue
        days = [(k, rows[k][1] / rows[k - 1][1] - 1) for k in range(start + 1, j + 1)]
        news_k, news_move = min(days, key=lambda x: x[1])
        if news_move > NEWS_DAY:                             # gradual slide: no news day
            j += 1
            continue
        low_k = min(range(j, min(n, start + WINDOW + 1)), key=lambda k: rows[k][1])
        nxt = [c for _, c in rows[low_k + 1:low_k + 4]]
        if nxt and max(nxt) / rows[low_k][1] - 1 > 0.8:      # snap-back: probably a bad print
            j += 1
            continue
        u = uni.get(cik, {})
        d = rows[news_k][0]
        out.append({"cik": cik, "name": u.get("name", ""), "ticker": u.get("ticker", ""), "date": d,
                    "month": time.strftime("%b %Y", time.strptime(d, "%Y-%m-%d")),
                    "start_date": rows[start][0], "end_date": rows[low_k][0],
                    "prev_close": f"{base:.4g}", "low_close": f"{rows[low_k][1]:.4g}",
                    "fall_pct": f"{(rows[low_k][1] / base - 1) * 100:.1f}",
                    "days": j - news_k + 1, "news_day_pct": f"{news_move * 100:.1f}",
                    "status": u.get("status", "")})
        j = low_k + COOLDOWN
out.sort(key=lambda r: r["date"], reverse=True)
with open(ROOT / "data" / "crashes.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["cik"])
    w.writeheader()
    w.writerows(out)
yrs = Counter(r["date"][:4] for r in out)
one_day = sum(1 for r in out if r["days"] == 1)
rep = ["# Crashes (fall of 50%+ within 5 trading days, with a news day of 25%+)",
       f"scanned {len(list((ROOT / 'data' / 'prices').glob('*.csv')))} price files", "",
       f"**{len(out)} crashes** ({one_day} crossed -50% in a single day)", "", "| Year | Crashes |", "|---|---|"] + \
      [f"| {y} | {n} |" for y, n in sorted(yrs.items())] + \
      ["", "Latest 15:", "", "| News day | Company | Ticker | Fall | Days |", "|---|---|---|---|---|"] + \
      [f"| {r['date']} | {r['name']} | {r['ticker']} | {r['fall_pct']}% | {r['days']} |" for r in out[:15]]
(ROOT / "reports" / "crashes.md").write_text("\n".join(rep) + "\n")
print("\n".join(rep[:12]))
