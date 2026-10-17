"""
Build the static site into ./site: web/ (page code) + site/data.json with both triages.

For every episode: the cells, call and chances (fit.py), the crash announcement (crashes), market value at entry,
and the total return if bought at the entry-day close (returns.py): one year later once the year is complete
(backtest: Winner >= +30%, Recovery 0 to +30%, Failure < 0), else to the latest close (screener). Every return is
shown next to the average of the 10 similar biotechs bought the same day (controls.py).

Track record (backtest episodes with complete inputs):
  by call      median, average (any single return capped at +500%), similar biotechs' average, share that beat them,
               share up 30%+ and down 80%+
  paths        median return day by day after entry, per call and for the similar biotechs
  by year      average 1-year return per entry year: Buy candidates, Avoid, and the Buy candidates' similar biotechs
  by cause     crashes only: the same numbers per cause of the crash (from the company's announcement)
  $10,000      $10,000 spread equally, day by day, over every open Buy candidate (bought at its entry close, sold a
               year later), against the same money in their similar biotechs; no trading costs
Live calls: data/ledger.csv, one row each time a recent episode (entry within 30 days) gets a call or a new call;
the build's time is the call time and the return is measured from the last close before it. The workflow publishes
new rows as a GitHub release, whose timestamp GitHub sets.
"""
import csv, json, shutil, statistics, time
from datetime import date, datetime, timezone
from common import DATA, ROOT, KINDS, closes, episodes, universe
from returns import STEPS, daily, path, plus, total_return

SITE = ROOT / "site"
TODAY = date.today().isoformat()
FIVE_YEARS_AGO = TODAY.replace(TODAY[:4], str(int(TODAY[:4]) - 5), 1)
LEDGER = DATA / "ledger.csv"
LCOLS = ["called_utc", "kind", "id", "cik", "ticker", "name", "call", "base_date"]
CALLS = ("overdone", "close", "justified")


def mean(v):
    return sum(v) / len(v) if v else None


def ctrl_return(ctrl, cid, d0, d1):
    rs = [min(r, 5.0) for r in (total_return(k, d0, d1)[0] for k in (ctrl.get(cid) or {}).get("ciks", [])) if r is not None]
    return mean(rs)                                      # each capped at +500%, like the averages of the calls


def ctrl_path(ctrl, cid, d0):
    ps = [p for p in (path(k, d0) for k in (ctrl.get(cid) or {}).get("ciks", [])) if p]
    return [mean([p[i] for p in ps]) for i in range(len(STEPS))] if ps else None


def stats(group, key="ret", ckey="ctrl365"):
    a = [r[key] for r in group]
    c = [r[ckey] for r in group if r.get(ckey) is not None]
    return {"n": len(a), "median": round(statistics.median(a), 1), "mean": round(mean([min(v, 500) for v in a]), 1),
            "ctrl_mean": round(mean(c), 1) if c else None,
            "beat": round(100 * sum(r[key] > r[ckey] for r in group if r.get(ckey) is not None) / len(c)) if c else None,
            "win30": round(100 * sum(v >= 30 for v in a) / len(a)), "wipe80": round(100 * sum(v <= -80 for v in a) / len(a))}


def portfolio(items, ctrl, calendar):
    """$10,000 split equally each day across open positions; idle cash earns nothing."""
    pos = [r for r in items if r["verdict"] == "overdone" and r.get("ret") is not None]
    if not pos:
        return None
    start = min(r["entry_date"] for r in pos)
    cal = [t for t in calendar if t >= start]
    legs = []
    for r in pos:
        d1 = min(plus(r["entry_date"], 365), TODAY)
        own = daily(r["cik"], r["entry_date"], d1, cal)
        cs = [daily(k, r["entry_date"], d1, cal) for k in (ctrl.get(r["id"]) or {}).get("ciks", [])]
        legs.append((r["entry_date"], d1, own, cs))
    v_own = v_ctl = 10000.0
    out = [[start, 10000, 10000]]
    for t in cal[1:]:
        open_ = [l for l in legs if l[0] < t <= l[1]]
        if open_:
            v_own *= 1 + mean([l[2].get(t, 0.0) for l in open_])
            v_ctl *= 1 + mean([mean([c.get(t, 0.0) for c in l[3]]) if l[3] else 0.0 for l in open_])
        out.append([t, round(v_own), round(v_ctl)])
    week = [p for i, p in enumerate(out) if i % 5 == 0 or i == len(out) - 1]
    return {"start": start, "positions": len(pos), "points": week, "own": out[-1][1], "ctrl": out[-1][2]}


def build(kind, ctrl, calendar):
    xd, xc = closes("XBI")
    items = []
    for c in sorted(episodes(kind), key=lambda c: c["cik"]):
        if c["date"] < FIVE_YEARS_AGO:
            continue
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
                        if k in ("factors", "verdict", "why", "insider_net", "mcap", "price", "chance_up", "chance_down", "cause")})
        rec["sources"].append({"label": "All SEC filings",
                               "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(c['cik'])}&type=&dateb=&owner=include&count=40"})
        entry_d = c["end_date"]
        one_year = plus(entry_d, 365)
        rec["entry_date"], rec["played"] = entry_d, one_year <= TODAY
        r, last = total_return(c["cik"], entry_d, one_year if rec["played"] else TODAY)
        if r is not None:
            rec["ret"], rec["ret_to"] = round(r * 100, 1), last
            cr = ctrl_return(ctrl, cid, entry_d, one_year if rec["played"] else TODAY)
            rec["ctrl_ret"] = round(cr * 100, 1) if cr is not None else None
            if rec["played"]:
                rec["outcome"] = "Winner" if rec["ret"] >= 30 else "Recovery" if rec["ret"] >= 0 else "Failure"
                rec["ctrl365"] = rec["ctrl_ret"]
            ds, _ = closes(c["cik"])
            if ds and ds[-1] < min(one_year, plus(TODAY, -7)):
                rec["stopped"] = ds[-1]
        if rec["played"]:
            p = path(c["cik"], entry_d)
            rec["path"] = [v * 100 for v in p] if p else None
            cp = ctrl_path(ctrl, cid, entry_d)
            rec["cpath"] = [v * 100 for v in cp] if cp else None
        items.append(rec)
    done = [r for r in items if r["verdict"] in CALLS and r.get("played") and r.get("path") and r.get("ret") is not None]
    track = {}
    for g in CALLS:
        grp = [r for r in done if r["verdict"] == g]
        if grp:
            track[g] = {"n": len(grp), "path": [round(statistics.median(r["path"][i] for r in grp), 1) for i in range(len(STEPS))]}
    wc = [r for r in done if r.get("cpath")]
    if wc:
        track["ctrl"] = {"n": len(wc), "path": [round(statistics.median(r["cpath"][i] for r in wc), 1) for i in range(len(STEPS))]}
    stat = {g: stats([r for r in done if r["verdict"] == g]) for g in CALLS if any(r["verdict"] == g for r in done)}
    years = {}
    for y in sorted({r["entry_date"][:4] for r in done}):
        b = [r for r in done if r["entry_date"][:4] == y and r["verdict"] == "overdone"]
        a = [r for r in done if r["entry_date"][:4] == y and r["verdict"] == "justified"]
        years[y] = {"buy": round(mean([min(r["ret"], 500) for r in b]), 1) if b else None, "buy_n": len(b),
                    "avoid": round(mean([min(r["ret"], 500) for r in a]), 1) if a else None, "avoid_n": len(a),
                    "ctrl": round(mean([r["ctrl365"] for r in b if r.get("ctrl365") is not None]), 1) if any(r.get("ctrl365") is not None for r in b) else None}
    causes = {}
    if kind == "crash":
        played = [r for r in items if r.get("played") and r.get("ret") is not None and r.get("cause")]
        for cz in ("Failed trial", "FDA rejection or delay", "Safety problem", "Share sale", "Other", "No announcement filed"):
            grp = [r for r in played if r["cause"] == cz]
            if grp:
                causes[cz] = stats(grp)
    calls = [r for r in items if r["verdict"] in CALLS]
    for r in items:
        r.pop("path", None)
        r.pop("cpath", None)
    dates = sorted(r["date"] for r in items)
    fmt = lambda d: time.strftime("%b %Y", time.strptime(d, "%Y-%m-%d"))
    print(f"{kind}: {len(items)} episodes, {sum(1 for r in items if r['played'])} with a full year, "
          f"{len(calls)} with a call, {sum(1 for r in items if r['verdict'] == 'tbu')} TBU")
    return {"range": f"{fmt(dates[0])} – {fmt(dates[-1])}" if dates else "", "items": items, "track": track, "stats": stat,
            "years": years, "causes": causes, "portfolio": portfolio(calls, ctrl, calendar)}


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
            if it["verdict"] not in CALLS or it["entry_date"] < plus(TODAY, -30) or latest.get((kind, it["id"])) == it["verdict"]:
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
    calendar, _ = closes("XBI")
    kinds = {k: build(k, ctrl, calendar) for k in KINDS}
    data = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "companies": len(universe()),
            "kinds": kinds, "live": ledger(kinds, ctrl)}
    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(ROOT / "web", SITE)
    (SITE / "data.json").write_text(json.dumps(data, separators=(",", ":")))
    (SITE / "_headers").write_text("/data.json\n  Cache-Control: no-cache\n")


if __name__ == "__main__":
    main()
