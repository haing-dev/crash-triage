"""
Build the static site into ./site: web/ (page code) + site/data.json with both triages.

For every episode: the cells and call (fit.py), the crash announcement (crashes), market value at entry, and the total
return if bought at the close of the call day (returns.py) - the entry day, or for a Watch upgraded to Buy candidate
by a later 13F, that 13F's filing day: one year later once the year is complete (backtest: Winner >=
+30%, Recovery 0 to +30%, Failure < 0), else to the latest close (screener). Every return is shown next to the average
of the 10 similar biotechs bought the same day (controls.py).

Track record (Buy candidates with complete inputs):
  line chart   average total return day by day for a year after the call (any single return capped at +500%), against
               their similar biotechs and the biotech index (XBI) over the same dates
  $10,000      per year of entry: $10,000 split equally over that year's Buy candidates, each held a year (or to the
               latest close if the year isn't over), against the same money in their similar biotechs and in XBI
  by cause     crashes only: one-year results per cause of the crash (from the company's announcement)
Live calls: data/ledger.csv, one row each time a recent episode (entry within 30 days) gets a call or a new call;
the build's time is the call time and the return is measured from the last close before it. The workflow publishes
new rows as a GitHub release, whose timestamp GitHub sets.
"""
import csv, json, shutil, statistics, time
from datetime import date, datetime, timezone
from common import DATA, ROOT, KINDS, closes, episodes, universe
from returns import STEPS, path, plus, total_return

SITE = ROOT / "site"
TODAY = date.today().isoformat()
LEDGER = DATA / "ledger.csv"
LCOLS = ["called_utc", "kind", "id", "cik", "ticker", "name", "call", "base_date"]
CALLS = ("overdone", "close", "justified")


def mean(v):
    return sum(v) / len(v) if v else None


def ctrl_return(ctrl, cid, d0, d1):
    rs = [min(r, 5.0) for r in (total_return(k, d0, d1)[0] for k in (ctrl.get(cid) or {}).get("ciks", [])) if r is not None]
    return mean(rs)                                      # each capped at +500%, like the averages of the calls


def stats(group):
    a = [r["ret"] for r in group]
    c = [r["ctrl365"] for r in group if r.get("ctrl365") is not None]
    return {"n": len(a), "median": round(statistics.median(a), 1), "mean": round(mean([min(v, 500) for v in a]), 1),
            "ctrl_mean": round(mean(c), 1) if c else None,
            "win30": round(100 * sum(v >= 30 for v in a) / len(a)), "wipe80": round(100 * sum(v <= -80 for v in a) / len(a))}


def series(ctrl, r):
    """Return at each step after entry (as far as today reaches): own, similar biotechs, XBI."""
    own, ctl, idx = [], [], []
    for k in STEPS:
        d1 = plus(r["call_date"], k)
        if d1 > TODAY:
            break
        a, _ = total_return(r["cik"], r["call_date"], d1)
        if a is None:
            break
        own.append(min(a, 5.0))
        ctl.append(ctrl_return(ctrl, r["id"], r["call_date"], d1))
        idx.append(total_return("XBI", r["call_date"], d1)[0])
    return own, ctl, idx


def avg_path(rows, i, carry=False):
    """Average over the positions at each step after buying. carry=True: a position bought less than a year ago keeps
    its latest value for the later steps, so the last point equals what the money is worth today."""
    out, n = [], max((len(s[i]) for s in rows), default=0)
    for k in range(n):
        v = [s[i][k] if len(s[i]) > k else (s[i][-1] if carry and s[i] else None) for s in rows]
        v = [x for x in v if x is not None]
        if not v:
            break
        out.append(round(mean(v) * 100, 1))
    return out


def build(kind, ctrl):
    items = []
    for c in sorted(episodes(kind), key=lambda c: c["cik"]):
        cid = f"{c['cik']}_{c['date']}"
        rec = {"id": cid, "cik": c["cik"], "name": c["name"], "ticker": c["ticker"], "date": c["date"], "month": c["month"],
               "move": float(c["fall_pct"]), "days": int(c["days"]), "news_day": float(c["news_day_pct"]),
               "peak_date": c["start_date"], "verdict": "tbu", "why": ["inputs not collected yet"], "sources": []}
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
            rec.update({k: v for k, v in json.loads(sc.read_text()).items()
                        if k in ("factors", "verdict", "why", "insider_net", "mcap", "price", "cause", "call_date", "upgraded")})
        rec["sources"].append({"label": "All SEC filings",
                               "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(c['cik'])}&type=&dateb=&owner=include&count=40"})
        entry_d = c["end_date"]
        call_d = rec.get("call_date") or entry_d
        one_year = plus(call_d, 365)
        rec["entry_date"], rec["call_date"], rec["played"] = entry_d, call_d, one_year <= TODAY
        r, last = total_return(c["cik"], call_d, one_year if rec["played"] else TODAY)
        if r is not None:
            rec["ret"], rec["ret_to"] = round(r * 100, 1), last
            cr = ctrl_return(ctrl, cid, call_d, one_year if rec["played"] else TODAY)
            rec["ctrl_ret"] = round(cr * 100, 1) if cr is not None else None
            if rec["played"]:
                rec["outcome"] = "Winner" if rec["ret"] >= 30 else "Recovery" if rec["ret"] >= 0 else "Failure"
                rec["ctrl365"] = rec["ctrl_ret"]
            ds, _ = closes(c["cik"])
            if ds and ds[-1] < min(one_year, plus(TODAY, -7)):
                rec["stopped"] = ds[-1]
        items.append(rec)
    buys = [r for r in items if r["verdict"] == "overdone" and r.get("ret") is not None]
    ser = {r["id"]: series(ctrl, r) for r in buys}
    played = [ser[r["id"]] for r in buys if r["played"] and len(ser[r["id"]][0]) == len(STEPS)]
    track = {"n": len(played), "own": avg_path(played, 0), "ctrl": avg_path(played, 1), "xbi": avg_path(played, 2)} if played else None
    years = {}
    for y in sorted({r["call_date"][:4] for r in buys}):
        grp = [r for r in buys if r["call_date"][:4] == y]
        ss = [ser[r["id"]] for r in grp if ser[r["id"]][0]]
        if not ss:
            continue
        pth = {"own": avg_path(ss, 0, True), "ctrl": avg_path(ss, 1, True), "xbi": avg_path(ss, 2, True)}
        fin = lambda k: round(10000 * (1 + pth[k][-1] / 100)) if pth[k] else None
        years[y] = {"n": len(ss), "open": sum(1 for r in grp if not r["played"]), "own": fin("own"), "ctrl": fin("ctrl"),
                    "xbi": fin("xbi"), "path": pth}
    causes = {}
    if kind == "crash":
        done = [r for r in items if r.get("played") and r.get("ret") is not None and r.get("cause")]
        for cz in ("Failed trial", "FDA rejection or delay", "Safety problem", "Share sale", "Other", "No announcement filed"):
            grp = [r for r in done if r["cause"] == cz]
            if grp:
                causes[cz] = stats(grp)
    dates = sorted(r["date"] for r in items)
    fmt = lambda d: time.strftime("%b %Y", time.strptime(d, "%Y-%m-%d"))
    print(f"{kind}: {len(items)} episodes, {sum(1 for r in items if r['played'])} with a full year, "
          f"{sum(1 for r in items if r['verdict'] in CALLS)} with a call, {sum(1 for r in items if r['verdict'] == 'tbu')} TBU, "
          f"{len(buys)} buy candidates")
    return {"range": f"{fmt(dates[0])} – {fmt(dates[-1])}" if dates else "", "items": items, "track": track,
            "years": years, "causes": causes, "steps": STEPS}


def ledger(data, ctrl):
    """Append new calls on recent episodes; return all rows with their result so far."""
    rows = list(csv.DictReader(LEDGER.open())) if LEDGER.exists() else []
    latest = {}
    for r in rows:
        latest[(r["kind"], r["id"])] = r["call"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new = []
    for kind, kd in data.items():
        for it in kd["items"]:
            if it["verdict"] not in CALLS or it["call_date"] < plus(TODAY, -30) or latest.get((kind, it["id"])) == it["verdict"]:
                continue
            ds, _ = closes(it["cik"])
            base = max((d for d in ds if d <= TODAY), default=None)
            if not base or base < plus(TODAY, -7):            # not trading now: nothing to call
                continue
            new.append({"called_utc": now, "kind": kind, "id": it["id"], "cik": it["cik"], "ticker": it["ticker"],
                        "name": it["name"], "call": it["verdict"], "base_date": base})
    if new:
        with LEDGER.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LCOLS)
            if not rows:
                w.writeheader()
            w.writerows(new)
        (ROOT / "new_calls.md").write_text("Calls published by the build at " + now + ":\n\n" + "\n".join(
            f"- {r['kind']} · {r['name']} ({r['ticker']}) · {r['call']} · measured from the close of {r['base_date']}" for r in new))
    out = []
    for r in rows + new:
        res, _ = total_return(r["cik"], r["base_date"], TODAY)
        cr = ctrl_return(ctrl, r["id"], r["base_date"], TODAY)
        out.append({**r, "ret": round(res * 100, 1) if res is not None else None, "ctrl_ret": round(cr * 100, 1) if cr is not None else None})
    print(f"live calls: {len(rows)} before, {len(new)} new")
    return sorted(out, key=lambda r: r["called_utc"], reverse=True)


def main():
    ctrl = json.loads((DATA / "controls.json").read_text()) if (DATA / "controls.json").exists() else {}
    kinds = {k: build(k, ctrl) for k in KINDS}
    data = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "companies": len(universe()),
            "kinds": kinds, "live": ledger(kinds, ctrl)}
    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(ROOT / "web", SITE)
    (SITE / "data.json").write_text(json.dumps(data, separators=(",", ":")))
    v = time.strftime("%Y%m%d%H%M", time.gmtime())          # new file names each build: browsers never keep old code
    idx = SITE / "index.html"
    idx.write_text(idx.read_text().replace('href="style.css"', f'href="style.css?v={v}"').replace('src="app.js"', f'src="app.js?v={v}"'))
    (SITE / "_headers").write_text("".join(f"{p}\n  Cache-Control: no-cache\n" for p in ("/", "/index.html", "/data.json", "/app.js", "/style.css")))


if __name__ == "__main__":
    main()
