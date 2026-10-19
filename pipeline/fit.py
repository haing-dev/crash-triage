"""
Turn each episode's inputs into cells and a call (Buy candidate / Watch / Avoid, or TBU while an input is still being
collected), for both triages with the same method; each learns its cell colours only from its own past episodes.

Outcome used for the colours: total return from the entry day to 365 days later (returns.py) minus the average return
of the 10 similar biotechs bought the same day (controls.py).

Cells (all as of the entry day; nothing later):
  safety    crash only, from the crash announcement: Reported Death / Negative Side Effects / Well Tolerated / N/A
  ev        EV / Cash = (market value + debt - cash) / cash, cash and debt from the latest balance sheet filed by entry
            (SEC XBRL, at most 9 months old); below 0 = worth less than its cash
  programs  other Phase 2+ trials the company leads (ClinicalTrials.gov)
  raise     terms if it sells shares (raises.py, inputs.py): Toxic (convertibles whose conversion price floats below the
            market, or warrants whose price resets) / Costly (last raise in 3 years came with warrants or at 15%+ below
            the previous close, or an equity line) / Friendly (an ATM programme or a clean last raise) / N/A
  insider   net open-market buying or selling in Form 4s (the call uses those filed by entry)
  funds     13F filers holding the company at 2%+ of their whole reported portfolio, in the latest quarter whose 13F
            deadline (45 days) had passed by entry: how many
  short     FINRA short interest at the latest settlement date 14+ days before entry, % of shares outstanding that day
            (N/A above 100%: the share count and the short interest then disagree)
Colours are learned walk-forward (refitted monthly on episodes whose year had ended): the cut-off and which side is
green for ev / programs / funds / short, and for raise terms whether the category did better or worse than similar
biotechs. Insider buying is green and selling red; safety follows the announcement's wording.

Call, a fixed rule, tested on the backtest (holds before and after July 2023, crashes and slides):
  Avoid          going-concern warning, share price under $1, or Toxic raise terms
  Buy candidate  none of those, market value at least $50M and EV / Cash at most 2 (valued at most 3x its cash, debt in)
  Watch          the rest
  TBU            an input for that day is still being collected
Cash runway is no longer used: the burn needed per-filing reads that can't be done the same way for every company.

Output: data/<kind>/scores/<id>.json, reports/fit_<kind>.md
"""
import csv, json, statistics as st
from bisect import bisect_right
from datetime import date
from functools import lru_cache
from common import DATA, ROOT, KINDS, closes, episodes, kdir, money
from returns import actual_close, plus, shares_on, splits, total_return
import controls, holdings

TODAY = date.today().isoformat()
MIN_TRAIN, SHRINK = 250, 30
GATE_MCAP, GATE_EV, DEEP = 5e7, 2.0, -0.15
KEYS = {"crash": ["safety", "ev", "programs", "raise", "insider", "funds", "top", "short"],
        "slide": ["ev", "programs", "raise", "insider", "funds", "top", "short"]}
TOP_N = 10
CONT = {"ev": "ev_x", "programs": "trials", "funds": "n_funds", "short": "short_pct"}
WORD = {"crash": "crash", "slide": "slide start"}
CAUSE = {"efficacy miss": "Failed trial", "regulatory rejection": "FDA rejection or delay", "regulatory delay": "FDA rejection or delay",
         "safety": "Safety problem", "financing / dilution": "Share sale", "commercial miss": "Other", "partner exit": "Other",
         "other": "Other"}


def months_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days / 30.44


# ---------------------------------------------------------------- shared data sets

@lru_cache(maxsize=None)
def raises(cik):
    p = DATA / "raises" / f"{cik}.json"
    return json.loads(p.read_text()) if p.exists() else None


@lru_cache(maxsize=None)
def fund_data():
    """Concentrated 13F holdings by company, and the date the data sets cover without a gap through."""
    cp, sp = DATA / "holdings" / "concentrated.csv", DATA / "state" / "holdings.json"
    if not (cp.exists() and sp.exists()):
        return {}, None
    by_cik = {}
    for r in csv.DictReader(cp.open()):
        by_cik.setdefault(r["cik"], []).append(r)
    spans = sorted(s for s in (holdings.span(u) for u in json.loads(sp.read_text()).get("read_conc", [])) if s)
    through = None
    for a, b in spans:
        if through is None or a <= plus(through, 1):
            through = max(through or b, b)
        else:
            break
    return by_cik, through


def quarter_before(d):
    """Latest quarter end whose 13F deadline (45 days later) is on or before d."""
    y = int(d[:4])
    ends = sorted(f"{yy}-{mm:02d}-{dd}" for yy in (y, y - 1) for mm, dd in ((12, 31), (9, 30), (6, 30), (3, 31)))
    return [e for e in ends if plus(e, 45) <= d][-1]


def quarter_of(d):
    """End of the calendar quarter containing day d."""
    y, m = int(d[:4]), int(d[5:7])
    q = (m - 1) // 3
    return f"{y}-{3 * q + 3:02d}-{[31, 30, 30, 31][q]}"


def funds(cik, asof):
    """13F filers holding the company at 2%+ of their portfolio at the end of the last quarter reported before the
    entry day, or at the end of the quarter the entry falls in (funds may buy just before or just after a fall)."""
    by_cik, through = fund_data()
    qb, qa = quarter_before(asof), quarter_of(asof)
    if through is None or through < plus(qb, 45):
        return None
    best = {}
    for r in by_cik.get(cik, []):
        if (r["period"] == qb and r["filed"] <= asof) or r["period"] == qa:
            k = r["filer_cik"]
            if k not in best or float(r["weight"]) > float(best[k]["weight"]):
                best[k] = r
    return {"before": qb, "after": qa, "after_pending": through < plus(qa, 45),
            "list": sorted(((r["filer_cik"], r["filer"], float(r["weight"]), r["period"]) for r in best.values()), key=lambda t: -t[2])}


@lru_cache(maxsize=None)
def short_rows(cik):
    p = DATA / "market" / "short_interest" / f"{cik}.csv"
    return sorted((r["settlement_date"], float(r["short_interest"])) for r in csv.DictReader(p.open())
                  if r.get("short_interest")) if p.exists() else []


@lru_cache(maxsize=None)
def short_through():
    p = DATA / "state" / "massive_extra.json"
    return (json.loads(p.read_text()).get("short_interest") or {}).get("through") if p.exists() else None


def short_interest(cik, asof):
    thr, last = short_through(), plus(asof, -14)
    if not thr or plus(thr, 20) < last:
        return None, "short interest not collected yet"
    rows = [r for r in short_rows(cik) if plus(last, -31) <= r[0] <= last]
    if not rows:
        return {"pct": None}, None
    sd, si = rows[-1]
    sh = shares_on(cik, sd)
    pct = si / sh * 100 if sh else None
    if pct is not None and pct > 100:
        pct = None
    return {"pct": pct, "settlement": sd}, None


# ---------------------------------------------------------------- features

def raise_terms(r):
    """(label or None, details for the hover)."""
    ft = r.get("financing_terms") or {}
    if ft.get("floating_convertible"):
        return "Toxic", "Convertible notes whose conversion price floats below the market: “" + ft["floating_convertible"][:240] + "”"
    if ft.get("price_reset_warrants"):
        return "Toxic", "Warrants whose price resets lower: “" + ft["price_reset_warrants"][:240] + "”"
    rs = [z for z in (raises(r["cik"]) or {}).get("raises", []) if plus(r["asof"], -1096) < z["filed"] <= r["asof"]]
    last = max((z for z in rs if z["type"] == "priced"), key=lambda z: z["filed"], default=None)
    atm = max((z for z in rs if z["type"] == "atm"), key=lambda z: z["filed"], default=None)
    lt = ""
    if last:
        ds, _ = closes(r["cik"])
        i = bisect_right(ds, plus(last["filed"], -1)) - 1
        prev = actual_close(r["cik"], ds[i]) if i >= 0 else None
        disc = last["price"] / prev - 1 if prev else None
        lt = (f"Last raise {last['filed']} ({last['form']}) at ${last['price']:g} a share"
              + (f", {abs(disc) * 100:.0f}% {'below' if disc < 0 else 'above'} the previous close" if disc is not None else "")
              + (", with warrants" if last["warrants"] else ", no warrants"))
        if last["warrants"] or (disc is not None and disc <= DEEP):
            return "Costly", lt
    if ft.get("equity_line"):
        return "Costly", "Equity line in place: “" + ft["equity_line"][:240] + "”"
    if atm:
        return "Friendly", f"ATM programme filed {atm['filed']} ({atm['form']})" + (f", up to {money(atm['atm_usd'])}" if atm.get("atm_usd") else "") \
            + ": it can sell at market price" + (f" · {lt}" if lt else "")
    if last:
        return "Friendly", lt
    return None, "No ATM programme, share sale or toxic instrument in the filings of the last 3 years"


def features(kind, r, event):
    x = {"id": r["id"], "cik": r["cik"], "date": r["date"], "asof": r["asof"], "raw": r}
    b, p = r.get("balance"), r.get("price")
    stale = not b or months_between(b["end"], r["asof"]) > 9
    x["cash"] = b["cash"] if b and b["cash"] > 0 and not stale else None
    x["px"] = actual_close(r["cik"], p["date"]) if p else None
    sh = None
    if r.get("shares") and r["shares"]["end"] >= plus(r["asof"], -274):
        sh = r["shares"]["shares"]
        for day, n, d_ in splits(r["cik"]) or []:
            if r["shares"]["end"] < day <= r["asof"]:
                sh *= n / d_
    x["mcap"] = x["px"] * sh if x["px"] and sh else None
    x["debt"] = (r.get("debt") or {}).get("usd") or 0.0
    x["ev_x"] = (x["mcap"] + x["debt"] - x["cash"]) / x["cash"] if x["mcap"] and x["cash"] else None
    t = r.get("trials")
    x["trials"] = t["other_trials"] if t else None
    ins = r.get("insiders") or {}
    net = lambda ts: sum(t["value"] if t["code"] == "P" else -t["value"] for t in ts)
    full = net(ins.get("trades", []))
    known = net([t for t in ins.get("trades", []) if t["filed"] <= r["asof"]])
    x["insider"] = "g" if known >= 10_000 else "r" if known <= -10_000 else "n"
    x["insider_net"] = x["insider_trade"] = None
    if abs(full) >= 10_000:
        side = "P" if full > 0 else "S"
        x["insider_net"] = full
        x["insider_trade"] = max((t for t in ins["trades"] if t["code"] == side), key=lambda t: t["value"])
    x["safety"], x["safety_txt"] = "n", None
    f = (event or {}).get("facts") or {}
    s = f.get("safety") or {}
    if (s.get("deaths") or 0) > 0:
        x["safety"], x["safety_txt"] = "r", "Reported Death"
    elif f.get("trigger") == "safety" or s.get("clinical_hold_or_trial_paused") or (s.get("serious_drug_related_patients") or 0) > 0:
        x["safety"], x["safety_txt"] = "r", "Negative Side Effects"
    elif s.get("described_as_well_tolerated"):
        x["safety"], x["safety_txt"] = "g", "Well Tolerated"
    x["cause"] = None
    if kind == "crash":
        x["cause"] = ("No announcement filed" if (event or {}).get("status") == "no filing found" else
                      CAUSE.get(f.get("trigger"), "Other") if f.get("related") is not False else "Other")
    x["raise"], x["raise_txt"] = raise_terms(r)
    x["gc"] = bool((r.get("going_concern") or {}).get("flag"))
    return x


def pending(kind, r, event, x):
    """Inputs still being collected for this episode's entry day (empty when complete)."""
    why = []
    if r.get("price") and x["px"] is None:
        why.append("actual share price not collected yet")
    if kind == "crash" and (not event or str(event.get("status", "")).startswith("error")):
        why.append("company announcement not read yet")
    rz = raises(r["cik"])
    if not rz or rz.get("checked", "") < r["asof"]:
        why.append("raise history not collected yet")
    fd = funds(r["cik"], r["asof"])
    si, w2 = short_interest(r["cik"], r["asof"])
    if w2:
        why.append(w2)
    x["fund"], x["si"] = fd, si
    x["n_funds"] = len(fd["list"]) if fd else None
    x["short_pct"] = (si or {}).get("pct")
    return why


def verdict_rule(x):
    hard, soft = [], []
    if x["gc"]:
        hard.append("going-concern warning")
    if x["px"] is not None and x["px"] < 1:
        hard.append("share price under $1")
    if x["raise"] == "Toxic":
        hard.append("toxic financing outstanding")
    if hard:
        return "justified", hard
    if x["mcap"] is None:
        soft.append("market cap unknown")
    elif x["mcap"] < GATE_MCAP:
        soft.append("market cap under $50M")
    if x["ev_x"] is None:
        soft.append("EV / cash unknown")
    elif x["ev_x"] > GATE_EV:
        soft.append(f"valued above {GATE_EV + 1:.0f}× cash")
    return ("close", soft) if soft else ("overdone", [])


# ---------------------------------------------------------------- learned colours

def cat(model, x, k):
    if k == "safety":
        return x["safety"]
    if k == "insider":
        return x["insider"]
    if k == "raise":
        return {"Friendly": "g", "Costly": "r", "Toxic": "r"}.get(x["raise"], "n")
    if k == "top":
        return "g" if x.get("top") else "n"
    m, v = model["cuts"].get(k), x[CONT[k]]
    if not m or v is None:
        return "n"
    return "g" if (v >= m["cut"]) == m["higher_is_better"] else "r"


def fit(train, keys):
    med = st.median(t["y"] for t in train)
    model = {"n": len(train), "cuts": {}, "points": {}}
    for k in keys:
        if k not in CONT:
            continue
        have = sorted((t[CONT[k]], t["y"]) for t in train if t[CONT[k]] is not None)
        best = None
        cuts = sorted({have[len(have) * q // 100][0] for q in range(20, 81, 5)}) if len(have) >= 40 else []
        for cut in cuts:
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
    return model


# ---------------------------------------------------------------- cell text

def days_word(d0, d):
    n = (date.fromisoformat(d) - date.fromisoformat(d0)).days
    return "same day" if n == 0 else f"{abs(n)} day{'s' if abs(n) > 1 else ''} {'after' if n > 0 else 'before'}"


def text(k, x):
    if k == "safety":
        return x["safety_txt"] or "N/A"
    if k == "ev":
        return "N/A" if x["ev_x"] is None else f"{x['ev_x']:.1f}×"
    if k == "programs":
        v = x["trials"]
        return "N/A" if not v else f"{v} other trial{'s' if v > 1 else ''}"
    if k == "raise":
        return x["raise"] or "N/A"
    if k == "insider":
        if x["insider_net"] is None:
            return "N/A"
        return f"{'Bought' if x['insider_net'] > 0 else 'Sold'} {money(abs(x['insider_net']))}, {days_word(x['date'], x['insider_trade']['date'])}"
    if k == "funds":
        n = x["n_funds"]
        return "N/A" if n is None else "None" if n == 0 else f"{n} fund{'s' if n > 1 else ''}"
    if k == "top":
        return "N/A" if x.get("top") is None else "Yes" if x["top"] else "No"
    if k == "short":
        v = x["short_pct"]
        return "N/A" if v is None else f"{v:.1f}%" if v < 10 else f"{v:.0f}%"


def bucket(k, x):
    if k == "safety":
        return x["safety_txt"] or "N/A"
    if k == "ev":
        v = x["ev_x"]
        return "N/A" if v is None else "Below cash" if v < 0 else "0–1×" if v < 1 else "1–2×" if v < 2 else "Over 2×"
    if k == "programs":
        v = x["trials"]
        return "N/A" if not v else "1 trial" if v == 1 else "2–4 trials" if v < 5 else "5+ trials"
    if k == "raise":
        return x["raise"] or "N/A"
    if k == "insider":
        return "N/A" if x["insider_net"] is None else "Bought" if x["insider_net"] > 0 else "Sold"
    if k == "funds":
        n = x["n_funds"]
        return "N/A" if n is None else "None" if n == 0 else "1 fund" if n == 1 else "2+ funds"
    if k == "top":
        return "N/A" if x.get("top") is None else "Yes" if x["top"] else "No"
    if k == "short":
        v = x["short_pct"]
        return "N/A" if v is None else "Under 5%" if v < 5 else "5–15%" if v < 15 else "15%+"


def note(k, x):
    """Hover text: where the cell comes from."""
    r = x["raw"]
    if k == "ev":
        if x["ev_x"] is None:
            return "Needs market value and a balance sheet from the last 9 months"
        return (f"(market value {money(x['mcap'])} + debt {money(x['debt'])} − cash {money(x['cash'])}) ÷ cash; "
                f"balance sheet {r['balance']['end']}")
    if k == "raise":
        return x["raise_txt"]
    if k == "insider" and x.get("insider_trade"):
        t = x["insider_trade"]
        return f"Largest: {t['who']} {money(t['value'])} on {t['date']} (Form 4 filed {t['filed']}). Trades from 90 days before to 30 days after; the call uses those filed by entry."
    if k == "funds" and x.get("fund"):
        f = x["fund"]
        tail = " (next quarter's 13F not filed yet)" if f["after_pending"] else ""
        if not f["list"]:
            return f"No 13F filer held it at 2%+ of its portfolio at the end of {f['before']} or {f['after']}{tail}"
        return "; ".join(f"{n} {w * 100:.1f}% ({q})" for _, n, w, q in f["list"][:8]) + tail
    if k == "top" and x.get("top"):
        return "; ".join(f"{n} (top {rank}: {hits} of {n_} plays up 30%+)" for n, rank, hits, n_ in x["top"])
    if k == "short" and x.get("si") and x["si"].get("settlement"):
        return f"FINRA short interest at settlement {x['si']['settlement']} ÷ shares outstanding that day"
    if k == "programs" and r.get("trials"):
        ex = [e["drugs"][0] for e in r["trials"]["examples"][:3] if e["drugs"]]
        return "Other Phase 2+ trials it leads (ClinicalTrials.gov)" + (f", e.g. {'; '.join(dict.fromkeys(ex))}" if ex else "")
    return ""


# ---------------------------------------------------------------- run

def prepare(kind, ctrl):
    keys = KEYS[kind]
    rows = []
    for c in episodes(kind):
        cid = f"{c['cik']}_{c['date']}"
        p = kdir(kind, "inputs") / f"{cid}.json"
        r = json.loads(p.read_text()) if p.exists() else None
        if not r or r.get("error") or r.get("version", 0) < 6 or r.get("asof") != c["end_date"]:
            rows.append({"id": cid, "tbu": {"id": cid, "verdict": "tbu", "why": ["inputs not collected yet"], "factors": {}}})
            continue
        ev = None
        if kind == "crash":
            q = DATA / "crash" / "events" / p.name
            ev = json.loads(q.read_text()) if q.exists() else None
        x = features(kind, r, ev)
        x["pending"] = pending(kind, r, ev, x)
        x["y"] = None
        d365 = plus(r["asof"], 365)
        if d365 <= TODAY:
            a, _ = total_return(r["cik"], r["asof"], d365)
            cs = [min(v, 5.0) for v in (total_return(k, r["asof"], d365)[0] for k in (ctrl.get(cid) or {}).get("ciks", [])) if v is not None]
            if a is not None:
                x["abs"], x["d365"] = a, d365
            if a is not None and cs:
                x["y"] = a - sum(cs) / len(cs)
        rows.append(x)
    return rows


MIN_COMPANIES, PRIOR_PLAYS, WEIGHT_CAP = 5, 5, 0.20


def top_performers(all_rows):
    """Rank 13F filers by their record on backtest episodes (both triages) they held at 2%+ of their portfolio.

    Score = conviction-weighted hit rate: each play counts with the fund's portfolio weight in the stock (capped at 20%,
    so one huge stake can't dominate), a hit = up 30%+ a year later (a giant winner counts once, like any other hit),
    shrunk toward the hit rate of all plays with the weight of 5 average plays, so a short record can't top the list.
    A fund needs plays in 5+ different companies to be ranked. as_of(d) uses only episodes whose year had ended by d,
    so a past call never sees later results."""
    done = sorted((x for x in all_rows if "tbu" not in x and x.get("d365") and x.get("fund")), key=lambda x: x["d365"])
    names = {}
    for x in all_rows:
        for fc, n, _, _ in ((x.get("fund") or {}).get("list") or []):
            names[fc] = n
    cache = {}

    def as_of(d):
        if d not in cache:
            plays = {}
            for x in done:
                if x["d365"] > d:
                    break
                hit = x["abs"] >= 0.3
                for fc, _, w, _ in x["fund"]["list"]:
                    plays.setdefault(fc, []).append((min(w, WEIGHT_CAP), hit, x["cik"]))
            allp = [p for v in plays.values() for p in v]
            if not allp:
                cache[d] = {}
                return cache[d]
            base = sum(w * h for w, h, _ in allp) / sum(w for w, _, _ in allp)
            wbar = sum(w for w, _, _ in allp) / len(allp)
            scored = []
            for fc, v in plays.items():
                if len({c for _, _, c in v}) < MIN_COMPANIES:
                    continue
                sw, sh = sum(w for w, _, _ in v), sum(w * h for w, h, _ in v)
                score = (sh + PRIOR_PLAYS * wbar * base) / (sw + PRIOR_PLAYS * wbar)
                scored.append((fc, score, sum(h for _, h, _ in v), len(v)))
            ranked = sorted(scored, key=lambda t: (-t[1], names.get(t[0], "")))[:TOP_N]
            cache[d] = {fc: (i + 1, sc, hits, n) for i, (fc, sc, hits, n) in enumerate(ranked)}
        return cache[d]
    return as_of, names


def score_kind(kind, rows):
    keys = KEYS[kind]
    known = sorted((x for x in rows if "tbu" not in x and x["y"] is not None and not x["pending"]), key=lambda x: x["d365"])
    out_dir = kdir(kind, "scores")
    for old in out_dir.glob("*.json"):
        old.unlink()
    full = fit(known, keys) if len(known) >= MIN_TRAIN else None
    models, held = {}, []
    for x in rows:
        if "tbu" in x:
            (out_dir / f"{x['id']}.json").write_text(json.dumps(x["tbu"]))
            continue
        m0 = x["asof"][:7] + "-01"
        if m0 not in models:
            tr = [t for t in known if t["d365"] <= m0]
            models[m0] = fit(tr, keys) if len(tr) >= MIN_TRAIN else None
        model = models[m0] or full
        verdict, why = verdict_rule(x)
        if x["pending"]:
            verdict, why = "tbu", x["pending"]

        def colour(k):
            if text(k, x) == "N/A" or not model:
                return "n"
            if k == "insider":
                return "g" if x["insider_net"] > 0 else "r"
            if k == "safety":
                return x["safety"]
            pts = model["points"][k].get(cat(model, x, k), 0.0)
            return "g" if pts >= 0.05 else "r" if pts <= -0.05 else "n"

        out = {"id": x["id"], "verdict": verdict, "why": why, "mcap": x["mcap"], "price": x["px"], "insider_net": x["insider_net"],
               "cause": x["cause"], "factors": {k: [colour(k), text(k, x), note(k, x), bucket(k, x)] for k in keys}}
        (out_dir / f"{x['id']}.json").write_text(json.dumps(out))
        if x["y"] is not None and verdict != "tbu":
            held.append((verdict, x["y"], x["abs"]))
    rep = [f"# Fit report: {kind}", f"episodes: {len(rows)} · TBU: {sum(1 for x in rows if 'tbu' in x or x.get('pending'))} · "
           f"with a full year and complete inputs: {len(known)}", "",
           "| Call | n | median | mean | similar biotechs mean | beat them | up 30%+ | down 80%+ |", "|---|---|---|---|---|---|---|---|"]
    for v in ("overdone", "close", "justified"):
        g = [(y, a) for gg, y, a in held if gg == v]
        if g:
            ys, aa = [y for y, _ in g], [a for _, a in g]
            rep.append(f"| {v} | {len(g)} | {st.median(aa) * 100:+.0f}% | {sum(min(a, 5) for a in aa) / len(aa) * 100:+.0f}% | "
                       f"{sum(a - y for y, a in g) / len(g) * 100:+.0f}% | {sum(y > 0 for y in ys) / len(ys):.0%} "
                       f"| {sum(a >= .3 for a in aa) / len(aa):.0%} | {sum(a <= -.8 for a in aa) / len(aa):.0%} |")
    (ROOT / "reports" / f"fit_{kind}.md").write_text("\n".join(rep) + "\n")
    print("\n".join(rep))


def run_all():
    c = controls.build()
    rows = {k: prepare(k, c) for k in KINDS}
    as_of, names = top_performers(rows["crash"] + rows["slide"])
    for k in KINDS:
        for x in rows[k]:
            if "tbu" in x:
                continue
            f = x.get("fund")
            if not f:
                x["top"] = None
                continue
            # a call that is still open uses today's ranking; a past one the ranking known on its entry day
            tops = as_of(TODAY if plus(x["asof"], 365) > TODAY else x["asof"][:7] + "-01")
            hit = sorted((tops[fc] + (fc,) for fc, *_ in f["list"] if fc in tops), key=lambda t: t[0])
            x["top"] = [(names.get(t[4], t[4]), t[0], t[2], t[3]) for t in hit]
        score_kind(k, rows[k])
    now = as_of(TODAY)
    (ROOT / "reports" / "top_performers.md").write_text(
        "# Top performer owners (as of today)\n\nConviction-weighted hit rate (portfolio weight capped at 20%; hit = up 30%+ a year "
        "later), shrunk toward the average of all plays; 5+ different companies needed.\n\n| Rank | Fund | Score | Plays up 30%+ | Plays |\n|---|---|---|---|---|\n"
        + "\n".join(f"| {r} | {names.get(fc, fc)} | {sc:.0%} | {h} | {n} |" for fc, (r, sc, h, n) in sorted(now.items(), key=lambda t: t[1][0])) + "\n")


if __name__ == "__main__":
    run_all()
