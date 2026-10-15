"""
Find the episodes each triage studies, from the stored daily closes (split-adjusted).

CRASH  the stock falls 50%+ within 5 trading days from the highest close of the 5 days before, and the biggest
       single-day fall in that window is 25%+ (the "news day" = crash date). Entry (where the backtest buys) is the
       trough: the lowest close of the window.
SLIDE  the stock is 30%+ below its highest close of the past 6 months (126 trading days) with no single day of
       -25% or worse on the way down: a slow decline, not a news crash. Entry is the first close 30% below the high.
       Left out when the company had a crash from a year before the high through the entry (that is a crash
       aftermath, already covered by the crash triage). A new slide can only start after the stock was back within
       30% of its 6-month high, so the tail of an earlier decline never counts as a fresh slide.

Shared filters: the high is at least $1 (penny-stock noise); a +80% snap-back within 3 days means a bad print.
One crash per company per 20 trading days; at most one slide per company per 126 trading days (6 months).

Output: data/crash/episodes.csv, data/slide/episodes.csv (same columns), reports/episodes.md
"""
import csv, time
from collections import Counter
from datetime import date, timedelta
from common import DATA, ROOT, kdir, universe

CRASH_FALL, CRASH_WINDOW, NEWS_DAY, CRASH_COOLDOWN = -0.50, 5, -0.25, 20
SLIDE_FALL, SLIDE_WINDOW, SLIDE_COOLDOWN = -0.30, 126, 126
COLS = ["cik", "name", "ticker", "date", "month", "start_date", "end_date", "prev_close", "low_close", "fall_pct",
        "days", "news_day_pct", "status"]


def row(cik, rows, start, entry, date_k, worst, kind_days):
    u = universe().get(cik, {})
    d = rows[date_k][0]
    return {"cik": cik, "name": u.get("name", ""), "ticker": u.get("ticker", ""), "date": d,
            "month": time.strftime("%b %Y", time.strptime(d, "%Y-%m-%d")), "start_date": rows[start][0],
            "end_date": rows[entry][0], "prev_close": f"{rows[start][1]:.4g}", "low_close": f"{rows[entry][1]:.4g}",
            "fall_pct": f"{(rows[entry][1] / rows[start][1] - 1) * 100:.1f}", "days": kind_days,
            "news_day_pct": f"{worst * 100:.1f}", "status": u.get("status", "")}


def snap_back(rows, k):
    nxt = [c for _, c in rows[k + 1:k + 4]]
    return bool(nxt) and max(nxt) / rows[k][1] - 1 > 0.8


def crashes(cik, rows):
    out, n, j = [], len(rows), 1
    while j < n:
        start = max(range(max(0, j - CRASH_WINDOW), j), key=lambda k: rows[k][1])
        base = rows[start][1]
        if base < 1 or rows[j][1] / base - 1 > CRASH_FALL:
            j += 1
            continue
        days = [(k, rows[k][1] / rows[k - 1][1] - 1) for k in range(start + 1, j + 1)]
        news_k, news_move = min(days, key=lambda x: x[1])
        low_k = min(range(j, min(n, start + CRASH_WINDOW + 1)), key=lambda k: rows[k][1])
        if news_move > NEWS_DAY or snap_back(rows, low_k):
            j += 1
            continue
        out.append(row(cik, rows, start, low_k, news_k, news_move, j - news_k + 1))
        j = low_k + CRASH_COOLDOWN
    return out


def slides(cik, rows, crash_dates):
    """A slide starts the first day the close is 30% below the 6-month high, counted only when the stock was above
    that line before (so the tail of an earlier decline or of a skipped crash never counts as a new slide)."""
    out, n, j, armed = [], len(rows), SLIDE_WINDOW, False
    while j < n:
        start = max(range(j - SLIDE_WINDOW, j), key=lambda k: rows[k][1])
        base = rows[start][1]
        if rows[j][1] / base - 1 > SLIDE_FALL:
            armed = True                      # back within 30% of the high: a new slide can start
            j += 1
            continue
        if not armed or base < 1 or (out and out[-1]["start_date"] == rows[start][0]):
            j += 1                            # not re-armed, penny stock, or the same decline (same high) as last time
            continue
        armed = False
        worst = min(rows[k][1] / rows[k - 1][1] - 1 for k in range(start + 1, j + 1))
        lo = (date.fromisoformat(rows[start][0]) - timedelta(days=365)).isoformat()
        if worst <= NEWS_DAY or snap_back(rows, j) or any(lo <= d <= rows[j][0] for d in crash_dates):
            j += 1                            # a crash (or bad print), not a slide
            continue
        out.append(row(cik, rows, start, j, j, worst, j - start))
        j += SLIDE_COOLDOWN
    return out


def main():
    found = {"crash": [], "slide": []}
    for p in sorted((DATA / "prices").glob("*.csv")):
        cik = p.stem
        if cik == "XBI":
            continue
        rows = [(r["date"], float(r["close"])) for r in csv.DictReader(p.open()) if r["close"]]
        c = crashes(cik, rows)
        found["crash"] += c
        found["slide"] += slides(cik, rows, [x["date"] for x in c])
    rep = ["# Episodes", f"scanned {len(list((DATA / 'prices').glob('*.csv')))} price files", ""]
    for kind, out in found.items():
        out.sort(key=lambda r: r["date"], reverse=True)
        with open(kdir(kind) / "episodes.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
            w.writerows(out)
        yrs = Counter(r["date"][:4] for r in out)
        rep += [f"## {kind}: {len(out)}", "", "| Year | Count |", "|---|---|"] + [f"| {y} | {n} |" for y, n in sorted(yrs.items())] + [""]
    (ROOT / "reports" / "episodes.md").write_text("\n".join(rep) + "\n")
    print("\n".join(rep))


if __name__ == "__main__":
    main()
