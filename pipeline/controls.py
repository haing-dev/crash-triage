"""
Similar biotechs: for every episode, the 10 companies in the universe most like it on its entry day (91 days after
the crash/slide was detected, when the call is made and the stock bought), measured only with what was known that day,
so the comparison asks "what if I had bought a similar biotech instead?".

  eligible   traded on the entry day (or within the 5 days before) and 6 months earlier; market value known
             (actual close x cover-page shares); not the company itself; no crash or slide entry of its own in the
             6 months up to that day
  similar    closest on two things, each as a percentile rank among the eligible companies that day:
             market value, and the 6-month price change before the day. Distance = sum of the two rank gaps.

Output: data/controls.json {episode id: {entry, ciks}} (rebuilt by fit.py every build)
"""
import json
from bisect import bisect_right
from common import DATA, KINDS, closes, episodes
from datetime import date
from returns import buy_day, mcap, plus

N = 10


def entries_by_cik():
    out = {}
    for k in KINDS:
        for c in episodes(k):
            out.setdefault(c["cik"], []).append(c["end_date"])
    return out


def day_candidates(d, ciks, busy):
    lo6 = plus(d, -182)
    cand = []
    for cik in ciks:
        ds, cs = closes(cik)
        i = bisect_right(ds, d) - 1
        j = bisect_right(ds, lo6) - 1
        if i < 0 or j < 0 or ds[i] < plus(d, -5) or ds[j] < plus(lo6, -8) or cs[j] <= 0:
            continue
        if any(lo6 <= e <= d for e in busy.get(cik, [])):
            continue
        m = mcap(cik, ds[i])
        if m:
            cand.append((cik, m, cs[i] / cs[j] - 1))
    if len(cand) < N + 1:
        return []
    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda k: vals[k])
        r = [0.0] * len(vals)
        for pos, k in enumerate(order):
            r[k] = pos / (len(vals) - 1)
        return r
    rm, rr = ranks([c[1] for c in cand]), ranks([c[2] for c in cand])
    return [(c[0], c[1], c[2], rm[k], rr[k]) for k, c in enumerate(cand)]


def build():
    busy = entries_by_cik()
    ciks = [p.stem for p in (DATA / "prices").glob("*.csv") if p.stem != "XBI"]
    p = DATA / "controls.json"
    out, by_day = {}, {}
    for k in KINDS:                                       # recomputed every build: data filled in later is used
        for c in episodes(k):
            d = buy_day(c["end_date"])
            if d <= date.today().isoformat():
                by_day.setdefault(d, []).append((f"{c['cik']}_{c['date']}", c["cik"]))
    for d, eps in sorted(by_day.items()):
        cand = day_candidates(d, ciks, busy)
        pos = {x[0]: x for x in cand}
        for cid, cik in eps:
            me = mcap(cik, d)
            ds, cs = closes(cik)
            i, j = bisect_right(ds, d) - 1, bisect_right(ds, plus(d, -182)) - 1
            if not me or i < 0 or j < 0 or not cand:
                out[cid] = {"entry": d, "ciks": []}
                continue
            r6 = cs[i] / cs[j] - 1
            # the episode's own rank among that day's eligible companies
            pm = sum(x[1] < me for x in cand) / len(cand)
            pr = sum(x[2] < r6 for x in cand) / len(cand)
            best = sorted((abs(x[3] - pm) + abs(x[4] - pr), x[0]) for x in cand if x[0] != cik)[:N]
            out[cid] = {"entry": d, "ciks": [b[1] for b in best]}
    p.write_text(json.dumps(out, separators=(",", ":")))
    return out


if __name__ == "__main__":
    o = build()
    print(len(o), "episodes,", sum(1 for v in o.values() if v["ciks"]), "with similar biotechs")
