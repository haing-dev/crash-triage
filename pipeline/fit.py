"""
Turn each episode's inputs into green / grey / red cells and a verdict, with cut-offs and weights learned from history.
Runs for both triages (crash, slide) with the same method; each learns from its own past episodes only.

Outcome learned: return from the entry day (crash trough / slide -30% day) to 365 days later, minus the biotech index
(XBI) over the same dates.

Factors (all as of the entry day; nothing later):
  safety    crash only, from the crash announcement: Reported Death / Negative Side Effects / Well Tolerated / N/A
  runway    months of cash, one formula for every company:
              liquid funds = unrestricted cash & equivalents + readily liquid marketable securities (latest balance
                             sheet) + cash raised after the balance sheet (by entry) + in-the-money warrants at entry
              monthly burn = (net cash used in operations + capex over the latest six months
                              + one-off receipts in that window - one-off payments in that window) / 6
              runway       = liquid funds / monthly burn - months from the balance sheet to entry
  value     market value at entry / liquid funds (actual price, never one adjusted for later splits)
  insider   net open-market buying (green) or selling (red) in Form 4s filed by entry
  programs  other Phase 2+ trials the company leads (ClinicalTrials.gov)
Cut-offs for runway / value / programs and the direction (which side is green) are learned; points per category =
median outcome of that category minus the overall median, shrunk toward 0 for small groups (x n/(n+30)).
Score = sum of points, shown 0-100 as its percentile among past episodes. The factor colours and score are learned
walk-forward (refitted monthly on episodes whose full year had ended). The verdict is a fixed rule (verdict_rule):
going-concern warning or runway under 12 months -> Justified; otherwise market cap >= $50M and value <= 3x cash ->
Potentially Oversold; otherwise Too Close To Call.

Output: data/<kind>/scores/<id>.json, reports/fit_<kind>.md
"""
import json, statistics as st
from bisect import bisect_right
from datetime import date, timedelta
from common import DATA, ROOT, KINDS, closes, kdir, money

TODAY = date.today().isoformat()
MIN_TRAIN, SHRINK = 250, 30
GATE_RUNWAY, GATE_MCAP, GATE_VALUE = 12, 5e7, 3.0


def verdict_rule(x):
    """The verdict is a fixed rule, the same for every company in both triages (thresholds chosen on the whole
    history and checked to hold in both halves, before and after July 2023):
      Justified            going-concern warning, or cash runway under 12 months (or unknown): the company has to
                           raise money at the bottom, and these rarely recover
      Potentially Oversold cash is fine AND market cap at least $50M AND valued at no more than 3x its cash
      Too Close To Call    cash is fine, but small or richly valued
    Returns (verdict, reasons)."""
    hard, soft = [], []
    if (x["raw"].get("going_concern") or {}).get("flag"):
        hard.append("going-concern warning")
    if x["runway_m"] is None:
        hard.append("cash runway unknown")
    elif x["runway_m"] < GATE_RUNWAY:
        hard.append(f"runway under {GATE_RUNWAY} months")
    if hard:
        return "justified", hard
    if x["mcap"] is None:
        soft.append("market cap unknown")
    elif x["mcap"] < GATE_MCAP:
        soft.append("market cap under $50M")
    if x["value_x"] is None:
        soft.append("value vs cash unknown")
    elif x["value_x"] > GATE_VALUE:
        soft.append(f"valued above {GATE_VALUE:.0f}x cash")
    return ("close", soft) if soft else ("overdone", [])
KEYS = {"crash": ["safety", "runway", "value", "insider", "programs"], "slide": ["runway", "value", "insider", "programs"]}
CONT = {"runway": "runway_m", "value": "value_x", "programs": "trials"}
WORD = {"crash": "crash", "slide": "slide start"}


def move(ds, cs, d0, d1):
    i, j = bisect_right(ds, d0), bisect_right(ds, d1) - 1
    i = i - 1 if i > 0 and ds[i - 1] == d0 else i
    if i >= len(ds) or j < i:
        return None
    return cs[j] / cs[i] - 1


def months_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days / 30.44


def actual_close(cik, d, adj):
    """Actual (not split-adjusted) close on day d: the stored split list undoes later splits (still-listed companies);
    Tiingo's raw closes for companies that stopped trading."""
    sp = DATA / "splits" / f"{cik}.json"
    if sp.exists():
        px = adj
        for day, num_, den in json.loads(sp.read_text())["splits"]:
            if day > d:
                px *= num_ / den
        return px
    p = DATA / "prices_raw" / f"{cik}.csv"
    if p.exists():
        for line in p.open():
            if line.startswith(d):
                return float(line.split(",")[1])
    return None


def shares_at(cik, sh, d):
    """Cover-page share count carried to day d through any split in between (a 1-for-50 reverse split divides it)."""
    n = sh["shares"]
    sp = DATA / "splits" / f"{cik}.json"
    if sp.exists():
        for day, num_, den in json.loads(sp.read_text())["splits"]:
            if sh["end"] < day <= d:
                n *= num_ / den
    return n


def num(v):
    return isinstance(v, (int, float)) and v > 0


def features(kind, r):
    x = {"id": r["id"], "date": r["date"], "asof": r["asof"], "raw": r}
    b, b6, sh, p, L = r.get("balance"), r.get("burn6"), r.get("shares"), r.get("price"), r.get("liquidity") or {}
    x["cash"] = b["cash"] if b and b["cash"] > 0 else None
    px = actual_close(r["cik"], p["date"], p["adj_close"]) if p else None
    x["mcap"] = px * shares_at(r["cik"], sh, r["asof"]) if px and sh else None
    # runway
    x["runway_m"] = x["burn_m"] = None
    stale = b and months_between(b["end"], r["asof"]) > 9           # no balance sheet in 9 months: delinquent filer
    if x["cash"] and b6 and not stale:
        x["oneoff_add"] = sum(i["amount_usd"] for i in L.get("one_off_receipts", []) if num(i.get("amount_usd")))
        x["oneoff_sub"] = sum(i["amount_usd"] for i in L.get("one_off_payments", []) if num(i.get("amount_usd")))
        x["fin_after"] = sum(i["net_proceeds_usd"] for i in L.get("financings_after_balance_sheet", [])
                             if num(i.get("net_proceeds_usd")) and b["end"] < (i.get("date") or "9") <= r["asof"])
        x["warrant_cash"] = sum(w["count"] * w["exercise_price_usd"] for w in L.get("warrants", [])
                                if px and num(w.get("count")) and num(w.get("exercise_price_usd"))
                                and w["exercise_price_usd"] < px and (w.get("expiry") or "9") > r["asof"])
        x["liquid"] = x["cash"] + x["fin_after"] + x["warrant_cash"]
        x["burn_m"] = (b6["cash_used_ops"] + b6["capex"] + x["oneoff_add"] - x["oneoff_sub"]) / 6
        x["runway_m"] = 999.0 if x["burn_m"] <= 0 else max(0.0, x["liquid"] / x["burn_m"] - months_between(b["end"], r["asof"]))
        x["read"] = bool(L)
    # value vs cash
    x["value_x"] = None
    if x["cash"] and x["mcap"] and 0.01 < x["mcap"] / x["cash"] < 500:
        x["value_x"] = x["mcap"] / x["cash"]
    # programs
    t = r.get("trials")
    x["trials"] = t["other_trials"] if t else None
    # insiders: the verdict uses trades filed by entry; the cell shows the whole window
    ins = r.get("insiders") or {}
    net = lambda ts: sum(t["value"] if t["code"] == "P" else -t["value"] for t in ts)
    known = net([t for t in ins.get("trades", []) if t["filed"] <= r["asof"]])
    x["insider"] = "g" if known >= 10_000 else "r" if known <= -10_000 else "n"
    full = net(ins.get("trades", []))
    x["insider_net"] = x["insider_trade"] = None
    if abs(full) >= 10_000:
        side = "P" if full > 0 else "S"
        x["insider_net"] = full
        x["insider_trade"] = max((t for t in ins["trades"] if t["code"] == side), key=lambda t: t["value"])
    # safety (crash announcement)
    x["safety"], x["safety_txt"] = "n", None
    f = (r.get("event") or {}).get("facts") or {}
    s = f.get("safety") or {}
    if (s.get("deaths") or 0) > 0:
        x["safety"], x["safety_txt"] = "r", "Reported Death"
    elif f.get("trigger") == "safety" or s.get("clinical_hold_or_trial_paused") or (s.get("serious_drug_related_patients") or 0) > 0:
        x["safety"], x["safety_txt"] = "r", "Negative Side Effects"
    elif s.get("described_as_well_tolerated"):
        x["safety"], x["safety_txt"] = "g", "Well Tolerated"
    return x


# ---------------------------------------------------------------- model

def cat(model, x, k):
    if k in ("safety", "insider"):
        return x[k] or "n"
    m, v = model["cuts"].get(k), x[CONT[k]]
    if not m or v is None:
        return "n"
    return "g" if (v >= m["cut"]) == m["higher_is_better"] else "r"


def score(model, keys, x):
    return sum(model["points"][k].get(cat(model, x, k), 0.0) for k in keys)


def fit(train, keys):
    med = st.median(t["y"] for t in train)
    model = {"n": len(train), "cuts": {}, "points": {}}
    for k in keys:
        if k not in CONT:
            continue
        col = CONT[k]
        have = sorted((t[col], t["y"]) for t in train if t[col] is not None)
        best = None
        cuts = sorted({have[len(have) * q // 100][0] for q in range(20, 81, 5)}) if len(have) >= 40 else []
        for cut in cuts:                                         # split on the value, so ties stay together
            lo, hi = [y for v, y in have if v < cut], [y for v, y in have if v >= cut]
            if min(len(lo), len(hi)) >= 0.2 * len(have):
                gap = st.median(hi) - st.median(lo)
                if best is None or abs(gap) > abs(best[1]):
                    best = (cut, gap)
        model["cuts"][k] = {"cut": best[0], "higher_is_better": best[1] > 0} if best else None
    for k in keys:
        cats = {}
        for t in train:
            cats.setdefault(cat(model, t, k), []).append(t["y"])
        model["points"][k] = {c: round((st.median(v) - med) * len(v) / (len(v) + SHRINK), 4) for c, v in cats.items()}
    model["scores"] = sorted(score(model, keys, t) for t in train)
    return model


# ---------------------------------------------------------------- cell text

def days_word(kind, d0, d):
    n = (date.fromisoformat(d) - date.fromisoformat(d0)).days
    return f"on {WORD[kind]} day" if n == 0 else f"{abs(n)} day{'s' if abs(n) > 1 else ''} {'after' if n > 0 else 'before'} {WORD[kind]}"


def text(kind, k, x):
    if k == "safety":
        return x["safety_txt"] or "N/A"
    if k == "runway":
        v = x["runway_m"]
        return "N/A" if v is None else "No Cash Burn" if v >= 999 else f"{v:.0f} mo"
    if k == "value":
        v = x["value_x"]
        return "N/A" if v is None else f"{'Below' if v < 1 else 'Above'} Cash ({v:.2f}x)" if v < 0.1 else f"{'Below' if v < 1 else 'Above'} Cash ({v:.1f}x)"
    if k == "insider":
        if x["insider_net"] is None:
            return "N/A"
        side = "Insider Buy" if x["insider_net"] > 0 else "Insider Sell"
        return f"{side} ({money(abs(x['insider_net']))}, {days_word(kind, x['date'], x['insider_trade']['date'])})"
    if k == "programs":
        v = x["trials"]
        return "N/A" if not v else f"{v} other trial{'s' if v > 1 else ''}"


def bucket(k, x):
    if k == "safety":
        return x["safety_txt"] or "N/A"
    if k == "runway":
        v = x["runway_m"]
        return ("N/A" if v is None else "No cash burn" if v >= 999 else "Under 6 mo" if v < 6 else "6–12 mo" if v < 12
                else "12–24 mo" if v < 24 else "24+ mo")
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
    eff = f"Past episodes like this: {model['points'][k].get(cat(model, x, k), 0.0) * 100:+.0f} pts vs typical"
    m = model["cuts"].get(k) if k in CONT else None
    if m:
        unit = {"runway": " months", "value": "× cash", "programs": " other trials"}[k]
        eff += f" (split at {m['cut']:.1f}{unit})" if k == "value" else f" (split at {m['cut']:.0f}{unit})"
    if k == "runway":
        bits = []
        if r.get("balance"):
            bits.append(f"Liquid funds {money(r['balance']['cash'])} at {r['balance']['end']}"
                        + (" (restricted cash removed)" if any(p.startswith("minus") for p in r["balance"]["parts"]) else ""))
        if x.get("fin_after"):
            bits.append(f"+ {money(x['fin_after'])} raised after the balance sheet")
        if x.get("warrant_cash"):
            bits.append(f"+ {money(x['warrant_cash'])} from in-the-money warrants")
        b6 = r.get("burn6")
        if b6 and x.get("burn_m") is not None:
            adj = (f", + {money(x['oneoff_add'])} one-off receipts added back" if x.get("oneoff_add") else "") + \
                  (f", − {money(x['oneoff_sub'])} one-off payments" if x.get("oneoff_sub") else "")
            bits.append(f"burn {money(abs(x['burn_m']))}/mo = (operations {money(b6['cash_used_ops'])} + capex {money(b6['capex'])}{adj}) / 6, "
                        f"{b6['start']} to {b6['end']}")
            if not x.get("read"):
                bits.append("one-offs, later financings and warrants not read yet")
        elif r.get("balance") and months_between(r["balance"]["end"], r["asof"]) > 9:
            bits.append("no balance sheet filed in the 9 months before entry")
        if (r.get("going_concern") or {}).get("flag"):
            bits.append("Going-concern warning in the filing")
        return " · ".join(bits + [eff])
    if k == "value" and x.get("mcap"):
        return f"Market value {money(x['mcap'])} at entry · {eff}"
    if k == "insider" and r.get("insiders"):
        t = x.get("insider_trade")
        who = f" · largest: {t['who']} {money(t['value'])} on {t['date']}" if t else ""
        return f"{len(r['insiders'].get('trades', []))} open-market trades, 90 days before to 30 days after{who} · verdict uses trades filed by entry · {eff}"
    if k == "programs" and r.get("trials"):
        ex = [e["drugs"][0] for e in r["trials"]["examples"][:2] if e["drugs"]]
        return (f"e.g. {'; '.join(dict.fromkeys(ex))} · " if ex else "") + eff
    return eff


# ---------------------------------------------------------------- run

def run(kind):
    keys = KEYS[kind]
    xd, xc = closes("XBI")
    rows = []
    for p in sorted(kdir(kind, "inputs").glob("*.json")):
        r = json.loads(p.read_text())
        if r.get("error") or r.get("version", 0) < 5:
            continue
        for sub, field in (("liquidity", "liquidity"), ("events", "event")):
            q = DATA / kind / sub / p.name
            if q.exists():
                j = json.loads(q.read_text())
                if not j.get("error"):
                    r[field] = j
        x = features(kind, r)
        d365 = (date.fromisoformat(r["asof"]) + timedelta(days=365)).isoformat()
        x["y"] = None
        if d365 <= TODAY:
            ds, cs = closes(r["cik"])
            a, b = move(ds, cs, r["asof"], d365), move(xd, xc, r["asof"], d365)
            if a is not None and b is not None:
                x["y"], x["abs"], x["d365"] = a - b, a, d365
        rows.append(x)
    known = sorted((x for x in rows if x["y"] is not None), key=lambda x: x["d365"])
    out_dir = kdir(kind, "scores")
    for old in out_dir.glob("*.json"):
        old.unlink()
    full = fit(known, keys) if len(known) >= MIN_TRAIN else None
    models, heldout = {}, []
    for x in rows:
        month0 = x["asof"][:7] + "-01"                                # refit monthly: outcomes known by the 1st
        if month0 not in models:
            train = [t for t in known if t["d365"] <= month0]
            models[month0] = fit(train, keys) if len(train) >= MIN_TRAIN else None
        model, ins = (models[month0], False) if models[month0] else (full, True)
        if not model:
            continue
        s = score(model, keys, x)
        verdict, why = verdict_rule(x)

        def colour(k):
            if text(kind, k, x) == "N/A":
                return "n"
            if k == "insider":
                return "g" if x["insider_net"] > 0 else "r"
            if k == "safety":
                return x["safety"]
            p = model["points"][k].get(cat(model, x, k), 0.0)
            return "g" if p >= 0.05 else "r" if p <= -0.05 else "n"

        out = {"id": x["id"], "score": round(100 * bisect_right(model["scores"], s) / len(model["scores"])),
               "verdict": verdict, "why": why, "in_sample": ins, "trained_on": model["n"], "mcap": x["mcap"], "insider_net": x["insider_net"],
               "factors": {k: [colour(k), text(kind, k, x), note(k, x, model), bucket(k, x)] for k in keys}}
        (out_dir / f"{x['id']}.json").write_text(json.dumps(out))
        if x["y"] is not None:
            heldout.append((verdict, x["y"], x["abs"], x["asof"]))
    rep = [f"# Fit report: {kind}", f"episodes with inputs: {len(rows)} · with a full year: {len(known)}", ""]
    if full:
        rep += ["## Model on all history", "", "| Factor | Cut-off | Points green / grey / red (pp vs median) |", "|---|---|---|"]
        for k in keys:
            c = full["cuts"].get(k) if k in CONT else None
            cs = f"{'≥' if c['higher_is_better'] else '<'} {c['cut']:.2f} is green" if c else ("categorical" if k not in CONT else "not enough data")
            pt = full["points"][k]
            rep.append(f"| {k} | {cs} | {pt.get('g', 0) * 100:+.1f} / {pt.get('n', 0) * 100:+.1f} / {pt.get('r', 0) * 100:+.1f} |")
    if heldout:
        rep += ["", "## Backtest (every episode with a full year; entry to +365 days; mean caps any single return at +500%)", "",
                "| Verdict | n | median | mean | XBI mean | beat XBI | up 30%+ | down 80%+ |", "|---|---|---|---|---|---|---|---|"]
        for v in ("overdone", "close", "justified"):
            g = [(y, a) for gg, y, a, _ in heldout if gg == v]
            if g:
                ys, aa = [y for y, _ in g], [a for _, a in g]
                mean = sum(min(a, 5) for a in aa) / len(aa)
                xmean = sum(a - y for y, a in g) / len(g)
                rep.append(f"| {v} | {len(g)} | {st.median(aa) * 100:+.0f}% | {mean * 100:+.0f}% | {xmean * 100:+.0f}% | {sum(y > 0 for y in ys) / len(ys):.0%} "
                           f"| {sum(a >= .3 for a in aa) / len(aa):.0%} | {sum(a <= -.8 for a in aa) / len(aa):.0%} |")
    (ROOT / "reports" / f"fit_{kind}.md").write_text("\n".join(rep) + "\n")
    print("\n".join(rep))


if __name__ == "__main__":
    for kind in KINDS:
        run(kind)
