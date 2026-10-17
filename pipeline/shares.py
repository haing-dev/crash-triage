"""
Shares outstanding over time for every company in the universe, from the cover pages of its 10-Q / 10-K filings
(SEC XBRL, dei:EntityCommonStockSharesOutstanding), with the date each figure was filed. Used for market value on any
past day (shares as last reported by that day x the actual close), e.g. to pick similar biotechs for comparison.

Output: data/shares/<cik>.json [[as_of, filed, shares], ...]; refreshed when older than 20 days.

    python shares.py --minutes 120
"""
import argparse, json, time
from datetime import date, timedelta
from common import DATA, get, universe


def history(cik):
    f = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json") or {}
    rows = (((f.get("facts") or {}).get("dei") or {}).get("EntityCommonStockSharesOutstanding") or {}).get("units", {}).get("shares", [])
    by = {}
    for x in rows:
        if x.get("val") and x.get("filed"):
            by.setdefault((x["end"], x["filed"], x.get("accn")), []).append(x["val"])
    out = {}
    for (end, filed, accn), vals in by.items():            # several share classes on one cover page are added
        k = (end, filed)
        out[k] = max(out.get(k, 0), sum(vals))
    return sorted([e, fd, v] for (e, fd), v in out.items())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=120)
    a = ap.parse_args()
    t0, d = time.time(), DATA / "shares"
    d.mkdir(exist_ok=True)
    fresh = (date.today() - timedelta(days=20)).isoformat()
    n = 0
    for cik in sorted(universe()):
        p = d / f"{cik}.json"
        if p.exists() and json.loads(p.read_text()).get("checked", "") >= fresh:
            continue
        if time.time() - t0 > a.minutes * 60:
            break
        try:
            p.write_text(json.dumps({"checked": date.today().isoformat(), "rows": history(cik)}))
            n += 1
        except Exception as e:
            print(cik, "failed", str(e)[:120])
    print(f"share histories refreshed: {n}")


if __name__ == "__main__":
    main()
