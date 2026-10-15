"""
Build the static site into ./site: web/ (page code) + site/data.json with both triages.

For every episode: the factor cells, score and verdict (fit.py), the crash announcement (crashes), market cap at
entry, and the return if bought at entry (crash trough / slide -30% day): one year later once the year is complete
(backtest table, tagged Winner >= +30%, Recovery 0 to +30%, Failure < 0), or to the latest close (screener table).
A company that stopped trading keeps its last price. Track record: median path from entry, per verdict, out-of-sample
backtest episodes only, with the biotech index (XBI) over the same dates.
"""
import json, shutil, statistics, time
from bisect import bisect_right
from datetime import date, timedelta
from common import DATA, ROOT, KINDS, closes, episodes, kdir, universe

SITE = ROOT / "site"
TODAY = date.today()
FIVE_YEARS_AGO = TODAY.replace(year=TODAY.year - 5).isoformat()
STEPS = list(range(0, 366, 5)) + ([365] if 365 % 5 else [])


def at_or_before(ds, cs, d):
    i = bisect_right(ds, d) - 1
    return (ds[i], cs[i]) if i >= 0 else (None, None)


def path_from(ds, cs, base_day):
    """% change from base_day to each step up to 365 days later; None until the year is complete."""
    if not ds:
        return None
    i = bisect_right(ds, base_day) - 1
    if i < 0:
        return None
    b0, base = ds[i], cs[i]
    if (date.fromisoformat(b0) + timedelta(days=365)).isoformat() > TODAY.isoformat():
        return None
    return [round((at_or_before(ds, cs, (date.fromisoformat(b0) + timedelta(days=k)).isoformat())[1] / base - 1) * 100, 1) for k in STEPS]


def build(kind):
    xd, xc = closes("XBI")
    items = []
    for c in sorted(episodes(kind), key=lambda c: c["cik"]):
        if c["date"] < FIVE_YEARS_AGO:
            continue
        cid = f"{c['cik']}_{c['date']}"
        rec = {"id": cid, "name": c["name"], "ticker": c["ticker"], "date": c["date"], "month": c["month"],
               "move": float(c["fall_pct"]), "days": int(c["days"]), "news_day": float(c["news_day_pct"]),
               "peak_date": c["start_date"], "verdict": "pending", "sources": []}
        ev = DATA / kind / "events" / f"{cid}.json"
        if ev.exists():
            e = json.loads(ev.read_text())
            f = e.get("facts") or {}
            if f.get("related") is not False and f.get("summary"):
                parts = [x.strip() for x in f["summary"].split("·") if x.strip() and "n/a" not in x.lower()]
                rec["summary"], rec["trigger"] = " · ".join(parts), f.get("trigger")
            ok = [q for k, q in (f.get("evidence") or {}).items() if q and (f.get("evidence_verified") or {}).get(k)]
            rec["quotes"] = list(dict.fromkeys(ok))[:3]
            if e.get("source"):
                rec["sources"].append({"label": "Company announcement (SEC filing)", "url": e["source"]["url"]})
            if e.get("status") == "no filing found":
                rec["summary"] = "No company announcement filed with the SEC around this date"
        sc = DATA / kind / "scores" / f"{cid}.json"
        if sc.exists():
            rec.update({k: v for k, v in json.loads(sc.read_text()).items() if k in ("factors", "score", "verdict", "in_sample", "insider_net", "mcap")})
        rec["sources"].append({"label": "All SEC filings",
                               "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(c['cik'])}&type=&dateb=&owner=include&count=40"})
        ds, cs = closes(c["cik"])
        entry_d, entry = c["end_date"], float(c["low_close"])
        one_year = (date.fromisoformat(entry_d) + timedelta(days=365)).isoformat()
        rec["entry_date"], rec["played"] = entry_d, one_year <= TODAY.isoformat()
        if ds and entry > 0:
            when, px = at_or_before(ds, cs, one_year if rec["played"] else TODAY.isoformat())
            if px:
                rec["ret"], rec["ret_to"] = round((px / entry - 1) * 100, 1), when
                if rec["played"]:
                    rec["outcome"] = "Winner" if rec["ret"] >= 30 else "Recovery" if rec["ret"] >= 0 else "Failure"
                if ds[-1] < min(one_year, (TODAY - timedelta(days=7)).isoformat()):
                    rec["stopped"] = ds[-1]
        if rec["played"]:
            rec["path"], xp = path_from(ds, cs, entry_d), path_from(xd, xc, entry_d)
            if xp:
                rec["xbi365"], rec["xbi_path"] = xp[-1], xp
        items.append(rec)
    track = {}
    oos = [r for r in items if r["verdict"] != "pending" and not r.get("in_sample") and r.get("played") and r.get("path")]
    for g in ("overdone", "close", "justified"):
        grp = [r for r in oos if r["verdict"] == g]
        if grp:
            track[g] = {"n": len(grp), "path": [round(statistics.median(r["path"][i] for r in grp), 1) for i in range(len(STEPS))]}
    with_x = [r for r in oos if r.get("xbi_path")]
    if with_x:
        track["xbi"] = {"n": len(with_x), "path": [round(statistics.median(r["xbi_path"][i] for r in with_x), 1) for i in range(len(STEPS))]}
    for r in items:
        r.pop("path", None)
        r.pop("xbi_path", None)
    dates = sorted(r["date"] for r in items)
    fmt = lambda d: time.strftime("%b %Y", time.strptime(d, "%Y-%m-%d"))
    print(f"{kind}: {len(items)} episodes, {sum(1 for r in items if r['played'])} with a full year, "
          f"{sum(1 for r in items if r['verdict'] != 'pending')} scored")
    return {"range": f"{fmt(dates[0])} – {fmt(dates[-1])}" if dates else "", "items": items, "track": track}


def main():
    data = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "companies": len(universe()),
            "kinds": {k: build(k) for k in KINDS}}
    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(ROOT / "web", SITE)
    (SITE / "data.json").write_text(json.dumps(data, separators=(",", ":")))
    (SITE / "_headers").write_text("/data.json\n  Cache-Control: no-cache\n")


if __name__ == "__main__":
    main()
