"""
Build the static site into ./site from the data files: web/ (page code) + site/data.json.
Returns are measured from day 30 after the crash (first trading day on/after) to day 365, using daily closes;
a company that stopped trading keeps its last price (and is labelled).
"""
import csv, json, shutil, time
from bisect import bisect_right
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
TODAY = date.today()
FIVE_YEARS_AGO = TODAY.replace(year=TODAY.year - 5).isoformat()
STEPS = list(range(0, 366, 5)) + ([365] if 365 % 5 else [])


def prices(key):
    p = ROOT / "data" / "prices" / f"{key}.csv"
    if not p.exists():
        return [], []
    rows = [(r["date"], float(r["close"])) for r in csv.DictReader(p.open()) if r["close"]]
    return [d for d, _ in rows], [c for _, c in rows]


def at_or_before(ds, cs, d):
    i = bisect_right(ds, d) - 1
    return (ds[i], cs[i]) if i >= 0 else (None, None)


def path_from(ds, cs, crash_date):
    """% change from day 30 to each step up to day 365. None if the year isn't complete yet."""
    if not ds:
        return None, None, None
    base_day = (date.fromisoformat(crash_date) + timedelta(days=30)).isoformat()
    i = bisect_right(ds, base_day) - 1
    if i + 1 < len(ds) and ds[i] != base_day:
        i += 1                                              # first trading day on/after day 30
    if i < 0 or i >= len(ds):
        return None, None, None
    b0, base = ds[i], cs[i]
    end_day = (date.fromisoformat(b0) + timedelta(days=335)).isoformat()   # day 30 + 335 = day 365
    if end_day > TODAY.isoformat():
        return None, None, None
    ended = None
    if ds[-1] < end_day:
        ended = f"stopped trading {ds[-1]}"
    path = []
    for k in STEPS:
        d = (date.fromisoformat(b0) + timedelta(days=round(k * 335 / 365))).isoformat()
        _, c = at_or_before(ds, cs, d)
        path.append(round((c / base - 1) * 100, 1))
    return path, ended, b0


xd, xc = prices("XBI")
uni = {u["cik"]: u for u in csv.DictReader((ROOT / "data" / "universe.csv").open())}
crashes = []
cpath = ROOT / "data" / "crashes.csv"
for c in (csv.DictReader(cpath.open()) if cpath.exists() else []):
    if c["date"] < FIVE_YEARS_AGO:
        continue
    cid = f"{c['cik']}_{c['date']}"
    rec = {"id": cid, "name": c["name"], "ticker": c["ticker"], "date": c["date"], "month": c["month"],
           "move": float(c["fall_pct"]), "days": int(c["days"]), "news_day": float(c["news_day_pct"]),
           "verdict": "pending"}
    ev = ROOT / "data" / "events" / f"{cid}.json"
    if ev.exists():
        e = json.loads(ev.read_text())
        f = e.get("facts") or {}
        if f.get("related") is not False and f.get("summary"):
            parts = [x.strip() for x in f["summary"].split("·")]
            parts = [x for x in parts if x and "n/a" not in x.lower()]   # drop empty slots like "n/a in n/a"
            rec["summary"], rec["trigger"] = " · ".join(parts), f.get("trigger")
        okq = [q for k, q in (f.get("evidence") or {}).items() if q and (f.get("evidence_verified") or {}).get(k)]
        rec["quotes"] = list(dict.fromkeys(okq))[:3]
        if e.get("source"):
            rec.setdefault("sources", []).append({"label": "Company announcement (SEC filing)", "url": e["source"]["url"]})
        if e.get("status") == "no filing found":
            rec["summary"] = "No company announcement filed with the SEC around this date"
    fx = ROOT / "data" / "factors" / f"{cid}.json"
    if fx.exists():
        fj = json.loads(fx.read_text())
        rec.update({k: fj[k] for k in ("factors", "score", "verdict", "in_sample") if k in fj})
        rec.setdefault("sources", []).extend(fj.get("sources", []))
    rec.setdefault("sources", []).append({"label": "All SEC filings",
        "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(c['cik'])}&type=&dateb=&owner=include&count=40"})
    ds, cs = prices(c["cik"])
    # return if bought at the trough (lowest close of the crash window): one year later, or to date
    trough_d, trough = c["end_date"], float(c["low_close"])
    one_year = (date.fromisoformat(trough_d) + timedelta(days=365)).isoformat()
    rec["trough_date"] = trough_d
    rec["played"] = one_year <= TODAY.isoformat()
    if ds and trough > 0:
        when, px = at_or_before(ds, cs, one_year if rec["played"] else TODAY.isoformat())
        if px:
            rec["ret_trough"] = round((px / trough - 1) * 100, 1)
            rec["ret_trough_to"] = when
            if rec["played"]:
                rec["outcome"] = "Winner" if rec["ret_trough"] >= 30 else "Recovery" if rec["ret_trough"] >= 0 else "Failure"
            if ds[-1] < (one_year if rec["played"] else (TODAY - timedelta(days=7)).isoformat()):
                rec["stopped"] = ds[-1]
    path, ended, b0 = path_from(ds, cs, c["date"])
    if path:
        rec["ret365"], rec["path"] = path[-1], path
        if ended:
            rec["ended"] = ended
        xp, _, _ = path_from(xd, xc, c["date"])
        if xp:
            rec["xbi365"], rec["xbi_path"] = xp[-1], xp
    crashes.append(rec)

# track record: median path per verdict group + the index over the same windows. Only verdicts given at the time
# (walk-forward), not in-sample ones.
import statistics
track = {}
oos = [r for r in crashes if r.get("verdict") in ("overdone", "close", "justified") and not r.get("in_sample")
       and r.get("played") and r.get("path")]
for g in ("overdone", "close", "justified"):
    grp = [r for r in oos if r["verdict"] == g]
    if grp:
        track[g] = {"n": len(grp), "path": [round(statistics.median(r["path"][i] for r in grp), 1) for i in range(len(STEPS))]}
scored = [r for r in oos if r.get("xbi_path")]
if scored:
    track["xbi"] = {"n": len(scored), "path": [round(statistics.median(r["xbi_path"][i] for r in scored), 1) for i in range(len(STEPS))]}
for r in crashes:
    r.pop("path", None)
    r.pop("xbi_path", None)
    if not r.get("played"):                      # outcomes and track record use the played-out table only
        r.pop("ret365", None)
        r.pop("xbi365", None)

dates = sorted(r["date"] for r in crashes)
fmt = lambda d: time.strftime("%b %Y", time.strptime(d, "%Y-%m-%d"))
data = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "range": f"{fmt(dates[0])} – {fmt(dates[-1])}" if dates else "",
        "companies": len(uni), "crashes": crashes, "track": track}
if SITE.exists():
    shutil.rmtree(SITE)
shutil.copytree(ROOT / "web", SITE)
(SITE / "data.json").write_text(json.dumps(data, separators=(",", ":")))
(SITE / "_headers").write_text("/data.json\n  Cache-Control: no-cache\n")
print(f"site built: {len(crashes)} crashes, {sum(1 for r in crashes if r.get('ret365') is not None)} with a full year, "
      f"{sum(1 for r in crashes if r['verdict'] != 'pending')} scored")
