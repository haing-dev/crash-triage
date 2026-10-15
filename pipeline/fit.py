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
with the model fitted on all history and flagged as in-sample (left out of the outcomes and track-record pages).

Output: data/factors/<id>.json, reports/fit.md
"""
import csv, json, statistics as st
from bisect import bisect_right
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW, EV, OUT = ROOT / "data" / "factors_raw", ROOT / "data" / "events", ROOT / "data" / "factors"
TODAY = date.today().isoformat()
MIN_TRAIN, SHRINK = 250, 30
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
    return (f"${a / 1e9:.1f}B" if a >= 1e9 else f"${a / 1e6:.0f}M" if a >= 1e7 else f"${a / 1e6:.1f}M" if a >= 1e6
            else f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:.0f}")


_raw = {}



def raw_close(cik, d, adj):
    """Actual (not split-adjusted) close on day d: the stored split list (adjusted close with later splits undone)
    if we have one, else Tiingo's raw closes (after the last raw day: same ratio as on that day)."""
    if cik not in _raw:
        p = ROOT / "data" / "prices_raw" / f"{cik}.csv"
        sp = ROOT / "data" / "splits" / f"{cik}.json"
        _raw[cik] = (dict((r["date"], float(r["close"])) for r in csv.DictReader(p.open()) if r["close"]) if p.exists() else {},
                     json.loads(sp.read_text())["splits"] if sp.exists() else None)
    closes, splits = _raw[cik]
    if splits is not None:
        px = adj
        for day, num, den in splits:
            if day > d:
                px *= num / den          # e.g. 1-for-50 reverse split later: actual price was 1/50 of the adjusted one
        return px
    if d in closes:
        return closes[d]
    if closes and d > max(closes):
        last = max(closes)
        ds, cs = prices(cik)
        i = bisect_right(ds, last) - 1
        if i >= 0 and ds[i] == last and cs[i] > 0:
            return adj * closes[last] / cs[i]
    return None


def months_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days / 30.44


def features(r):
    """Numbers + categories from one raw record (None = unknown). Everything is as of the trough (r["asof"])."""
    x = {"id": r["id"], "date": r["date"], "asof": r["asof"], "final": r.get("final")}
    b, bu, sh, p, fl = r.get("balance"), r.get("burn"), r.get("shares"), r.get("price"), r.get("filing") or {}
    x["cash"] = b["cash"] if b and b["cash"] > 0 else None
    # cash runway: the company's own guidance if its latest 10-Q/10-K gives one (it knows its planned spending,
    # step-ups and cuts); otherwise cash / trailing-12-month operating burn, minus months since the balance sheet
    x["runway_m"], x["runway_src"] = None, None
    if fl.get("guided_until"):
        x["runway_m"], x["runway_src"] = max(0.0, months_between(r["asof"], fl["guided_until"])), "guidance"
    elif x["cash"] and bu:
        per = bu.get("ttm_per_month") if bu.get("ttm_per_month") is not None else bu["per_month"]
        if per >= 0:
            x["runway_m"], x["runway_src"] = 999.0, "no burn"
        else:
            x["runway_m"] = max(0.0, x["cash"] / -per - months_between(b["end"], r["asof"]))
            x["runway_src"] = "calculated"
    x["value_x"] = None
    px = raw_close(r["cik"], p["date"], p["adj_close"]) if p else None   # actual price at the trough
    # Never the adjusted price as is: it embeds later reverse splits, and "a split came later" is future
    # information that would leak into the verdict.
    if x["cash"] and sh and px:
        mcap = px * sh["shares"]
        if 0.01 < mcap / x["cash"] < 500:
            x["value_x"], x["mcap"] = mcap / x["cash"], mcap
    t = r.get("trials")
    x["trials"] = t["other_trials"] if t else None
    # insiders: the verdict only uses trades filed by the trough; the cell shows the whole window (90 days before
    # the news day to 30 days after)
    ins = r.get("insiders")
    x["insider"], x["insider_net"], x["insider_trade"] = None, None, None
    if ins is not None and "trades" in ins:
        net = lambda ts: sum(t["value"] if t["code"] == "P" else -t["value"] for t in ts)
        known = net([t for t in ins["trades"] if t["filed"] <= r["asof"]])
        x["insider"] = "g" if known >= 10_000 else "r" if known <= -10_000 else "n"
        full = net(ins["trades"])
        if abs(full) >= 10_000:
            side = "P" if full > 0 else "S"
            big = max((t for t in ins["trades"] if t["code"] == side), key=lambda t: t["value"])
            x["insider_net"], x["insider_trade"] = full, big
    x["safety"], x["safety_txt"] = None, None
    e = EV / f"{r['id']}.json"
    if e.exists():
        f = json.loads(e.read_text()).get("facts") or {}
        s = f.get("safety") or {}
        if f:
            if (s.get("deaths") or 0) > 0:
                x["safety"], x["safety_txt"] = "r", "Reported Death"
            elif f.get("trigger") == "safety" or s.get("clinical_hold_or_trial_paused") or (s.get("serious_drug_related_patients") or 0) > 0:
                x["safety"], x["safety_txt"] = "r", "Negative Side Effects"
            elif s.get("described_as_well_tolerated"):
                x["safety"], x["safety_txt"] = "g", "Well Tolerated"
            else:
                x["safety"] = "n"
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
        model["points"][k] = {c: round((st.median(v) - med) * len(v) / (len(v) + SHRINK), 4) for c, v in cats.items()}
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


def days_word(news, d):
    n = (date.fromisoformat(d) - date.fromisoformat(news)).days
    return "on crash day" if n == 0 else f"{abs(n)} day{'s' if abs(n) > 1 else ''} {'after' if n > 0 else 'before'} crash"


def text(k, x):
    if k == "safety":
        return x.get("safety_txt") or "N/A"
    if k == "runway":
        v = x["runway_m"]
        return "N/A" if v is None else "No Cash Burn" if x["runway_src"] == "no burn" else f"{v:.0f} mo"
    if k == "value":
        v = x["value_x"]
        return "N/A" if v is None else f"{'Below' if v < 1 else 'Above'} Cash ({v:.2f}x)" if v < 0.1 else f"{'Below' if v < 1 else 'Above'} Cash ({v:.1f}x)"
    if k == "insider":
        if x["insider_net"] is None:
            return "N/A"
        side = "Insider Buy" if x["insider_net"] > 0 else "Insider Sell"
        return f"{side} ({money(abs(x['insider_net']))}, {days_word(x['date'], x['insider_trade']['date'])})"
    if k == "programs":
        v = x["trials"]
        return "N/A" if not v else f"{v} other trial{'s' if v > 1 else ''}"


def bucket(k, x):
    """Group used by the column filter."""
    if k == "safety":
        return text(k, x)
    if k == "runway":
        v = x["runway_m"]
        return ("N/A" if v is None else "No cash burn" if x["runway_src"] == "no burn" else "Under 6 mo" if v < 6
                else "6–12 mo" if v < 12 else "12–24 mo" if v < 24 else "24+ mo")
    if k == "value":
        v = x["value_x"]
        return "N/A" if v is None else "Below Cash" if v < 1 else "Above Cash"
    if k == "insider":
        return "N/A" if x["insider_net"] is None else "Insider Buy" if x["insider_net"] > 0 else "Insider Sell"
    if k == "programs":
        v = x["trials"]
        return "N/A" if not v else "1 trial" if v == 1 else "2–4 trials" if v < 5 else "5+ trials"


def note(k, x, model):
    r = x["raw"]
    pts = model["points"][k].get(cat(model, x, k), 0.0) * 100
    eff = f"Past crashes like this: {pts:+.0f} pts vs typical"
    m = model["cuts"].get(k) if k in CONT else None
    if m:
        unit = {"runway": " months", "value": "× cash", "programs": " other trials"}[k]
        c = f"{m['cut']:.1f}" if k == "value" else f"{m['cut']:.0f}"
        eff += f" (split at {c}{unit})"
    fl = r.get("filing") or {}
    if k == "runway":
        bits = []
        if r.get("balance"):
            bits.append(f"Cash {money(r['balance']['cash'])} at {r['balance']['end']}"
                        + (" (restricted cash removed)" if any(p.startswith("minus") for p in r["balance"]["parts"]) else ""))
        bu = r.get("burn") or {}
        if bu.get("ttm_per_month") is not None or bu.get("per_month") is not None:
            per = bu.get("ttm_per_month") if bu.get("ttm_per_month") is not None else bu["per_month"]
            bits.append(f"{'burn' if per < 0 else 'operating cash inflow'} {money(abs(per))}/mo (last 12 months)"
                        + (f", {money(abs(bu['quarter_per_month']))}/mo last quarter" if bu.get("quarter_per_month") is not None else ""))
        if x["runway_src"] == "guidance":
            bits.append(f"Company guidance: \u201c…{fl['guidance'][-160:]}\u201d")
        if fl.get("going_concern"):
            bits.append("Going-concern warning in the filing")
        if fl.get("atm"):
            bits.append("Has an at-the-market (ATM) share programme" + (f", {money(fl['atm_remaining'])} left" if fl.get("atm_remaining") else ""))
        return " · ".join(bits + [eff])
    if k == "value" and x.get("mcap"):
        return f"Market value {money(x['mcap'])} at the trough · {eff}"
    if k == "insider" and r.get("insiders"):
        i = r["insiders"]
        t = x.get("insider_trade")
        who = f" · largest: {t['who']} {money(t['value'])} on {t['date']}" if t else ""
        return f"{len(i.get('trades', []))} open-market trades, 90 days before to 30 days after the news{who} · verdict uses trades filed by the trough · {eff}"
    if k == "programs" and r.get("trials"):
        ex = [e["drugs"][0] for e in r["trials"]["examples"][:2] if e["drugs"]]
        return (f"e.g. {'; '.join(dict.fromkeys(ex))} · " if ex else "") + eff
    return eff


def main():
    xd, xc = prices("XBI")
    rows = []
    for p in sorted(RAW.glob("*.json")):
        r = json.loads(p.read_text())
        if r.get("error"):
            continue
        x = features(r)
        x["raw"] = r
        if r.get("version", 1) < 3:
            continue                                             # old-format record: wait for re-collection
        t0 = r["asof"]                                           # the trough: where the backtest buys
        d365 = (date.fromisoformat(t0) + timedelta(days=365)).isoformat()
        x["y"] = None
        if d365 <= TODAY:
            ds, cs = prices(r["cik"])
            a, b = move(ds, cs, t0, d365), move(xd, xc, t0, d365)
            if a is not None and b is not None:
                x["y"], x["abs"], x["d365"] = a - b, a, d365
        rows.append(x)
    known = sorted((x for x in rows if x["y"] is not None), key=lambda x: x["d365"])
    OUT.mkdir(parents=True, exist_ok=True)
    first = fit(known) if len(known) >= MIN_TRAIN else None
    models, heldout = {}, []
    for x in rows:
        train = [t for t in known if t["d365"] <= x["asof"]]          # only outcomes known at this crash's trough
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
        def colour(k):
            if text(k, x) == "N/A":
                return "n"                                             # unknown is always shown neutral
            if k == "insider":
                return "g" if x["insider_net"] > 0 else "r"
            if k == "safety":
                return x["safety"] or "n"
            p = model["points"][k].get(cat(model, x, k), 0.0)
            return "g" if p >= 0.05 else "r" if p <= -0.05 else "n"   # better / worse than typical by 5+ points
        out = {"id": x["id"], "score": pct, "verdict": verdict, "in_sample": ins, "trained_on": model["n"],
               "factors": {k: [colour(k), text(k, x), note(k, x, model), bucket(k, x)] for k in KEYS},
               "insider_net": x["insider_net"]}
        (OUT / f"{x['id']}.json").write_text(json.dumps(out, indent=1))
        if x["y"] is not None and not ins:
            heldout.append((verdict, x["y"], x["abs"]))
    # report
    final = fit(known) if len(known) >= MIN_TRAIN else None
    rep = ["# Fit report", f"crashes with factor inputs: {len(rows)} · with a full year: {len(known)}", ""]
    if final:
        rep += ["## Model on all history (what new crashes get)", "", "| Factor | Cut-off | Points green / grey / red (pp vs median) |", "|---|---|---|"]
        for k in KEYS:
            c = final["cuts"].get(k) if k in CONT else None
            cs = f"{'≥' if c['higher_is_better'] else '<'} {c['cut']:.2f} is green" if c else ("categorical" if k not in CONT else "not enough data")
            pt = final["points"][k]
            rep.append(f"| {k} | {cs} | {pt.get('g', 0) * 100:+.1f} / {pt.get('n', 0) * 100:+.1f} / {pt.get('r', 0) * 100:+.1f} |")
    if heldout:
        rep += ["", "## Out-of-sample check (walk-forward: verdict given at the trough; return trough to +365 days, vs XBI)", "",
                "| Verdict | n | median stock move | median vs XBI | beat XBI | up 50%+ | down 80%+ |", "|---|---|---|---|---|---|---|"]
        for v in ("overdone", "close", "justified"):
            g = [(y, a) for gg, y, a in heldout if gg == v]
            if g:
                ys, aa = [y for y, _ in g], [a for _, a in g]
                rep.append(f"| {v} | {len(g)} | {st.median(aa) * 100:+.0f}% | {st.median(ys) * 100:+.0f}% | {sum(y > 0 for y in ys) / len(ys):.0%} "
                           f"| {sum(a >= .5 for a in aa) / len(aa):.0%} | {sum(a <= -.8 for a in aa) / len(aa):.0%} |")
    (ROOT / "reports" / "fit.md").write_text("\n".join(rep) + "\n")
    print("\n".join(rep))


if __name__ == "__main__":
    main()
