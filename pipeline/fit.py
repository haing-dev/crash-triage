"""
Turn raw factor inputs into green / grey / red cells and a verdict, with cut-offs and weights learned from history.

Outcome to explain: stock move from day 30 to day 365 minus the biotech index (XBI) over the same dates.

1. Each factor is put into a category per crash:
     safety    red if the announcement reports a clinical hold, deaths or serious drug-related events, or the trigger
               is safety; green if it says "well tolerated"; grey otherwise
     runway    months of cash left at day 30           -> cut-off learned from data (one cut: above / below)
     value     market value / cash at day 30          -> cut-off learned from data
     insider   net open-market buying (green), net selling (red), none (grey)
     programs  other Phase 2+ trials running          -> cut-off learned from data
   Which side of a cut-off is green is also learned (higher median outcome = green).
2. Points per category = median outcome of that category minus the overall median, shrunk toward 0 for small groups
   (x n/(n+30)). Score = sum of points, shown 0-100 as its percentile among past crashes.
3. Verdict: top third of past scores = "possibly overdone", bottom third = "justified", middle = "too close to call".

Walk-forward: each crash is scored with a model fitted only on crashes whose full year had ended by that crash's
day 30 — the verdict it would have got at the time. Crashes before enough history existed (MIN_TRAIN) are scored
with the first available model and flagged as in-sample.

Output: data/factors/<id>.json, reports/fit.md
"""
import csv, json, statistics as st
from bisect import bisect_right
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW, EV, OUT = ROOT / "data" / "factors_raw", ROOT / "data" / "events", ROOT / "data" / "factors"
TODAY = date.today().isoformat()
MIN_TRAIN, SHRINK = 120, 30
KEYS = ["safety", "runway", "value", "insider", "programs"]
CONT = {"runway": "runway_m", "value": "value_x", "programs": "trials"}


def prices(key):
    p = ROOT / "data" / "prices" / f"{key}.csv"
    rows = [(r["date"], float(r["close"])) for r in csv.DictReader(p.open()) if r["close"]] if p.exists() else []
    return [d for d, _ in rows], [c for _, c in rows]


def move(ds, cs, d0, d1):
    i, j = bisect_right(ds, d0), bisect_right(ds, d1) - 1
    i = i - 1 if i > 0 and ds[i - 1] == d0 else i
    if i >= len(ds) or j < i:
        return None
    return cs[j] / cs[i] - 1


def money(v):
    a = abs(v)
    return f"${a / 1e9:.1f}B" if a >= 1e9 else f"${a / 1e6:.0f}M" if a >= 1e7 else f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}K"


def features(r):
    """Numbers + safety/insider categories from one raw record (None = unknown)."""
    x = {"id": r["id"], "date": r["date"], "asof": r["asof"], "final": r.get("final")}
    b, bu, sh, p = r.get("balance"), r.get("burn"), r.get("shares"), r.get("price")
    x["cash"] = b["cash"] if b and b["cash"] > 0 else None
    x["runway_m"] = None
    if x["cash"] and bu:
        if bu["per_month"] >= 0:
            x["runway_m"] = 120.0
        else:
            x["runway_m"] = max(0.0, x["cash"] / -bu["per_month"] - (date.fromisoformat(r["asof"]) - date.fromisoformat(b["end"])).days / 30.44)
    x["value_x"] = None
    if x["cash"] and sh and p:
        mcap = p["adj_close"] / sh["split_factor"] * sh["shares"]
        if 0.01 < mcap / x["cash"] < 500:
            x["value_x"], x["mcap"] = mcap / x["cash"], mcap
    t = r.get("trials")
    x["trials"] = t["other_trials"] if t else None
    ins = r.get("insiders")
    if ins is None:
        x["insider"] = None
    elif ins["bought"] > 0 and ins["bought"] >= ins["sold"]:
        x["insider"] = "g"
    elif ins["sold"] > ins["bought"]:
        x["insider"] = "r"
    else:
        x["insider"] = "n"
    x["safety"] = None
    e = EV / f"{r['id']}.json"
    if e.exists():
        f = json.loads(e.read_text()).get("facts") or {}
        s = f.get("safety") or {}
        if f:
            bad = f.get("trigger") == "safety" or s.get("clinical_hold_or_trial_paused") or (s.get("deaths") or 0) > 0 \
                  or (s.get("serious_drug_related_patients") or 0) > 0
            x["safety"] = "r" if bad else "g" if s.get("described_as_well_tolerated") else "n"
            x["safety_txt"] = ("Clinical hold" if s.get("clinical_hold_or_trial_paused") else "Deaths reported" if (s.get("deaths") or 0) > 0
                               else "Serious side effects" if (s.get("serious_drug_related_patients") or 0) > 0
                               else "Safety issue" if f.get("trigger") == "safety" else "Well tolerated" if x["safety"] == "g"
                               else "None reported")
    return x


def fit(train):
    """Learn cut-offs, direction and points from crashes with a known outcome."""
    ys = [t["y"] for t in train]
    med = st.median(ys)
    model = {"median": med, "n": len(train), "cuts": {}, "points": {}}
    for k, col in CONT.items():
        vals = sorted(t[col] for t in train if t[col] is not None)
        best = None
        if len(vals) >= 40:
            for q in range(20, 81, 5):
                cut = vals[len(vals) * q // 100]
                hi = [t["y"] for t in train if t[col] is not None and t[col] >= cut]
                lo = [t["y"] for t in train if t[col] is not None and t[col] < cut]
                if min(len(hi), len(lo)) < 0.2 * len(vals):
                    continue
                gap = st.median(hi) - st.median(lo)
                if best is None or abs(gap) > abs(best[1]):
                    best = (cut, gap)
        model["cuts"][k] = {"cut": best[0], "higher_is_better": best[1] > 0} if best else None
    for k in KEYS:
        cats = {}
        for t in train:
            cats.setdefault(cat(model, t, k), []).append(t["y"])
        model["points"][k] = {c: round((st.median(v) - med) * len(v) / (len(v) + SHRINK), 4) for c, v in cats.items() if c != "n"}
        model["points"][k]["n"] = 0.0
    model["scores"] = sorted(score(model, t) for t in train)
    s = model["scores"]
    model["cut_lo"], model["cut_hi"] = s[len(s) // 3], s[2 * len(s) // 3]
    return model


def cat(model, x, k):
    if k in ("safety", "insider"):
        return x[k] or "n"
    m, v = model["cuts"].get(k), x[CONT[k]]
    if not m or v is None:
        return "n"
    return "g" if (v >= m["cut"]) == m["higher_is_better"] else "r"


def score(model, x):
    return sum(model["points"][k].get(cat(model, x, k), 0.0) for k in KEYS)


def text(k, x):
    if k == "safety":
        return x.get("safety_txt") or "n/a"
    if k == "runway":
        v = x["runway_m"]
        return "n/a" if v is None else "Cash positive" if v >= 120 else f"{v:.0f} mo"
    if k == "value":
        v = x["value_x"]
        return "n/a" if v is None else f"{v:.1f}× cash"
    if k == "insider":
        r = x["raw"].get("insiders")
        if not r:
            return "n/a"
        return f"{money(r['bought'])} bought" if x["insider"] == "g" else f"{money(r['sold'] - r['bought'])} sold" if x["insider"] == "r" else "None"
    if k == "programs":
        v = x["trials"]
        return "n/a" if v is None else "None" if v == 0 else f"{v} other trial{'s' if v > 1 else ''}"


def note(k, x, model):
    r = x["raw"]
    m = model["cuts"].get(k) if k in CONT else None
    side = ""
    if m:
        unit = {"runway": " months", "value": "× cash", "programs": " trials"}[k]
        c = f"{m['cut']:.1f}" if k == "value" else f"{m['cut']:.0f}"
        side = f"History: {'≥' if m['higher_is_better'] else '<'} {c}{unit} did better"
    if k == "runway" and r.get("balance") and r.get("burn"):
        return f"Cash {money(r['balance']['cash'])} at {r['balance']['end']}, burn {money(-r['burn']['per_month'])}/mo · {side}"
    if k == "value" and x.get("mcap"):
        return f"Market value {money(x['mcap'])} on day 30 · {side}"
    if k == "insider" and r.get("insiders"):
        i = r["insiders"]
        return f"{i['form4s']} Form 4 filings, news day to day 30" + (f" · buyers: {', '.join(i['buyers'][:2])}" if i["buyers"] else "")
    if k == "programs" and r.get("trials"):
        ex = [", ".join(e["drugs"][:1]) for e in r["trials"]["examples"][:2] if e["drugs"]]
        return (f"e.g. {'; '.join(ex)} · " if ex else "") + side
    return side


def main():
    xd, xc = prices("XBI")
    rows = []
    for p in sorted(RAW.glob("*.json")):
        r = json.loads(p.read_text())
        if r.get("error"):
            continue
        x = features(r)
        x["raw"] = r
        d30 = (date.fromisoformat(r["date"]) + timedelta(days=30)).isoformat()
        d365 = (date.fromisoformat(r["date"]) + timedelta(days=365)).isoformat()
        x["y"] = None
        if d365 <= TODAY:
            ds, cs = prices(r["cik"])
            a, b = move(ds, cs, d30, d365), move(xd, xc, d30, d365)
            if a is not None and b is not None:
                x["y"], x["d365"] = a - b, d365
        rows.append(x)
    known = sorted((x for x in rows if x["y"] is not None), key=lambda x: x["d365"])
    OUT.mkdir(parents=True, exist_ok=True)
    first = fit(known[:MIN_TRAIN]) if len(known) >= MIN_TRAIN else None
    models, heldout = {}, []
    for x in rows:
        d30 = (date.fromisoformat(x["date"]) + timedelta(days=30)).isoformat()
        train = [t for t in known if t["d365"] <= d30]
        if len(train) >= MIN_TRAIN:
            key = len(train)
            models.setdefault(key, fit(train) if key not in models else models[key])
            model, ins = models[key], False
        elif first:
            model, ins = first, True
        else:
            continue
        s = score(model, x)
        verdict = "overdone" if s >= model["cut_hi"] else "justified" if s <= model["cut_lo"] else "close"
        pct = round(100 * bisect_right(model["scores"], s) / len(model["scores"]))
        out = {"id": x["id"], "score": pct, "verdict": verdict, "in_sample": ins, "trained_on": model["n"],
               "factors": {k: [cat(model, x, k) if x.get(k if k in ("safety", "insider") else CONT[k]) is not None else "n",
                               text(k, x), note(k, x, model)] for k in KEYS}}
        (OUT / f"{x['id']}.json").write_text(json.dumps(out, indent=1))
        if x["y"] is not None and not ins:
            heldout.append((verdict, x["y"]))
    # report
    final = fit(known) if len(known) >= MIN_TRAIN else None
    rep = ["# Fit report", f"crashes with factor inputs: {len(rows)} · with a full year: {len(known)}", ""]
    if final:
        rep += ["## Model on all history (what new crashes get)", "", "| Factor | Cut-off | Points green / red (pp vs median) |", "|---|---|---|"]
        for k in KEYS:
            c = final["cuts"].get(k) if k in CONT else None
            cs = f"{'≥' if c['higher_is_better'] else '<'} {c['cut']:.2f} is green" if c else ("categorical" if k not in CONT else "not enough data")
            pt = final["points"][k]
            rep.append(f"| {k} | {cs} | {pt.get('g', 0) * 100:+.1f} / {pt.get('r', 0) * 100:+.1f} |")
    if heldout:
        rep += ["", "## Out-of-sample check (walk-forward: verdict given at the time, outcome vs XBI)", "",
                "| Verdict | n | median vs XBI | share beating XBI |", "|---|---|---|---|"]
        for v in ("overdone", "close", "justified"):
            ys = [y for g, y in heldout if g == v]
            if ys:
                rep.append(f"| {v} | {len(ys)} | {st.median(ys) * 100:+.0f}% | {sum(y > 0 for y in ys) / len(ys):.0%} |")
    (ROOT / "reports" / "fit.md").write_text("\n".join(rep) + "\n")
    print("\n".join(rep))


if __name__ == "__main__":
    main()
