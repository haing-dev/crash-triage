"""
Turn each episode's inputs into cells, a call (Buy candidate / Watch / Avoid, or TBU while an input is still being
collected) and two chances, for both triages with the same method; each learns only from its own past episodes.

Outcome learned: total return from the entry day to 365 days later (returns.py) minus the average return of the 10
similar biotechs bought the same day (controls.py).

Cells (all as of the entry day; nothing later):
  safety      crash only, from the crash announcement: Reported Death / Negative Side Effects / Well Tolerated / N/A
  runway      months of cash: liquid funds (cash + liquid securities + cash raised after the balance sheet +
              in-the-money warrants) / monthly burn (six months of operating cash use + capex, one-offs removed)
              - months from the balance sheet to entry
  ev          EV / Cash = (market value + debt - liquid funds) / liquid funds; below 0 = worth less than its cash
  programs    other Phase 2+ trials the company leads (ClinicalTrials.gov)
  raise       terms if it sells shares, from its filings (raises.py, inputs.py):
                Toxic    convertibles whose conversion price floats below the market, or warrants whose price resets
                Costly   the SEC small-company cap (public float under $75M: a third of it a year) is below the cash it
                         needs for the next 2 years; or its last raise (3 years) came with warrants or at 15%+ below
                         the previous close; or an equity line
                Friendly otherwise, with an ATM programme or a clean last raise on file
  insider     net open-market buying or selling in Form 4s (the call uses those filed by entry)
  specialists 13F filers with half their reported value in this universe across 5+ of its companies: their holdings
              in the latest quarter whose 13F deadline (45 days) had passed by entry vs the quarter before:
              Bought / Held / Sold (more funds adding than cutting, or the reverse)
  short       FINRA short interest at the latest settlement date 14+ days before entry, % of shares outstanding that
              day (N/A above 100%: the share count and the short interest then disagree)
Colours are learned walk-forward (refitted monthly on episodes whose year had ended): the cut-off and which side is
green for runway / ev / programs / short, and for raise terms and specialist funds whether that category did better
(green) or worse (red) than similar biotechs. Insider buying is green and selling red; safety follows the wording.

Call, a fixed rule:
  Avoid          going-concern warning, runway under 12 months or unknown, share price under $1, or it must raise
                 within 2 years (runway under 24 months) on Costly or Toxic terms
  Buy candidate  none of those, market value at least $50M and EV / Cash at most 2 (= valued at most 3x its cash,
                 debt included)
  Watch          the rest
  TBU            an input for that day is still being collected (listed in the reasons)
Chances: two logistic regressions on the cells' categories (plus market value, price under $1, going concern and,
for crashes, the cause), refitted each month on episodes whose year had ended: the chance of being up 30%+ and
down 80%+ one year after entry. A chance column is shown only while its walk-forward check passes (in each band of
predictions, 0-20 / 20-40 / 40-100%, with 20+ cases, what happened is within 10 points of the band).

Output: data/<kind>/scores/<id>.json, reports/fit_<kind>.md
"""
import csv, json, statistics as st
from bisect import bisect_right
from datetime import date
from functools import lru_cache
import numpy as np
from common import DATA, ROOT, KINDS, closes, episodes, kdir, money
from returns import actual_close, plus, shares_on, splits, total_return
import controls, holdings

TODAY = date.today().isoformat()
MIN_TRAIN, SHRINK = 250, 30
GATE_RUNWAY, GATE_MCAP, GATE_EV, RAISE_MONTHS, DEEP = 12, 5e7, 2.0, 24, -0.15
KEYS = {"crash": ["safety", "runway", "ev", "programs", "raise", "insider", "specialists", "short"],
        "slide": ["runway", "ev", "programs", "raise", "insider", "specialists", "short"]}
CONT = {"runway": "runway_m", "ev": "ev_x", "programs": "trials", "short": "short_pct"}
WORD = {"crash": "crash", "slide": "slide start"}
CAUSE = {"efficacy miss": "Failed trial", "regulatory rejection": "FDA rejection or delay", "regulatory delay": "FDA rejection or delay",
         "safety": "Safety problem", "financing / dilution": "Share sale", "commercial miss": "Other", "partner exit": "Other",
         "other": "Other"}


def months_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days / 30.44


def num(v):
    return isinstance(v, (int, float)) and v > 0


# ---------------------------------------------------------------- shared data sets

@lru_cache(maxsize=None)
def raises(cik):
    p = DATA / "raises" / f"{cik}.json"
    return json.loads(p.read_text()) if p.exists() else None


@lru_cache(maxsize=None)
def specialist_data():
    """(13F filings by filer, positions by accession, date the data sets cover without a gap through)."""
    fp, pp, sp = DATA / "holdings" / "filings.csv", DATA / "holdings" / "positions.csv", DATA / "state" / "holdings.json"
    if not (fp.exists() and pp.exists() and sp.exists()):
        return {}, {}, None
    by_filer = {}
    for r in csv.DictReader(fp.open()):
        by_filer.setdefault(r["filer_cik"], []).append(r)
    pos = {}
    for r in csv.DictReader(pp.open()):
        pos.setdefault(r["accession"], {})[r["cik"]] = float(r["shares"])
    spans = sorted(s for s in (holdings.span(u) for u in json.loads(sp.read_text()).get("read", [])) if s)
    through = None
    for a, b in spans:
        if through is None or a <= plus(through, 1):
            through = max(through or b, b)
        else:
            break
    return by_filer, pos, through


def quarter_before(d):
    """Latest quarter end whose 13F deadline (45 days later) is on or before d, and the quarter before it."""
    y = int(d[:4])
    ends = sorted(f"{yy}-{mm:02d}-{dd}" for yy in (y, y - 1, y - 2) for mm, dd in ((12, 31), (9, 30), (6, 30), (3, 31)))
    ends = [e for e in ends if plus(e, 45) <= d]
    return ends[-1], ends[-2]


def specialists(cik, asof):
    by_filer, pos, through = specialist_data()
    q1, q0 = quarter_before(asof)
    if through is None or through < plus(q1, 45):
        return None, "13F holdings for the quarter not published yet"
    inc = dec = held = 0
    for filer, fl in by_filer.items():
        f1 = [r for r in fl if r["period"] == q1 and r["filed"] <= asof]
        if not f1:
            continue
        f0 = [r for r in fl if r["period"] == q0 and r["filed"] <= asof]
        s1 = pos.get(max(f1, key=lambda r: r["filed"])["accession"], {}).get(cik, 0)
        s0 = pos.get(max(f0, key=lambda r: r["filed"])["accession"], {}).get(cik, 0) if f0 else 0
        for day, n, d_ in splits(cik) or []:
            if q0 < day <= q1:
                s0 *= n / d_
        held += s1 > 0
        inc += s1 > s0 * 1.0001
        dec += s1 < s0 * 0.9999
    if not held and not dec:
        return {"label": "None", "funds": 0}, None
    label = "Bought" if inc > dec else "Sold" if dec > inc else "Held"
    return {"label": label, "funds": held, "up": inc, "down": dec, "quarter": q1}, None


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
    sh = shares_on(cik, sd)                               # shares on the settlement day (before any later split)
    pct = si / sh * 100 if sh else None
    if pct is not None and pct > 100:                     # more shares short than exist: the two sources disagree
        pct = None
    return {"pct": pct, "settlement": sd}, None


# ---------------------------------------------------------------- features

def raise_terms(x, r):
    """Terms if the company sells shares (module notes). Returns (label or None, cell text)."""
    ft = r.get("financing_terms") or {}
    if ft.get("floating_convertible"):
        return "Toxic", "Toxic: notes convert below market price"
    if ft.get("price_reset_warrants"):
        return "Toxic", "Toxic: warrants with price resets"
    rs = [z for z in (raises(r["cik"]) or {}).get("raises", []) if plus(r["asof"], -1096) < z["filed"] <= r["asof"]]
    last = max((z for z in rs if z["type"] == "priced"), key=lambda z: z["filed"], default=None)
    atm = max((z for z in rs if z["type"] == "atm"), key=lambda z: z["filed"], default=None)
    need = max(0.0, RAISE_MONTHS * x["burn_m"] - x["liquid"]) if x.get("burn_m") and x["burn_m"] > 0 and x.get("liquid") else 0.0
    pf = (r.get("public_float") or {}).get("usd")
    if pf and pf < 75e6 and need > pf / 3:
        return "Costly", f"Costly: only {money(pf / 3)} a year at market price"
    dtxt = ""
    if last:
        ds, _ = closes(r["cik"])
        i = bisect_right(ds, plus(last["filed"], -1)) - 1
        prev = actual_close(r["cik"], ds[i]) if i >= 0 else None
        disc = last["price"] / prev - 1 if prev else None
        if disc is not None:
            dtxt = f"{abs(disc) * 100:.0f}% {'below' if disc < 0 else 'above'} market"
        if last["warrants"] or (disc is not None and disc <= DEEP):
            return "Costly", "Costly: last raise " + ", ".join(([dtxt] if dtxt else []) + (["with warrants"] if last["warrants"] else []))
    if ft.get("equity_line"):
        return "Costly", "Costly: equity line"
    if atm:
        return "Friendly", "Friendly: ATM at market price"
    if last:
        return "Friendly", "Friendly: last raise " + ", ".join(([dtxt] if dtxt else []) + ["no warrants"])
    return None, "N/A"


def features(kind, r, liq, event):
    x = {"id": r["id"], "cik": r["cik"], "date": r["date"], "asof": r["asof"], "raw": r}
    b, b6, p, L = r.get("balance"), r.get("burn6"), r.get("price"), liq or {}
    x["cash"] = b["cash"] if b and b["cash"] > 0 else None
    px = actual_close(r["cik"], p["date"]) if p else None
    x["px"] = px
    sh = None
    if r.get("shares") and r["shares"]["end"] >= plus(r["asof"], -274):   # cover page within 9 months, through splits
        sh = r["shares"]["shares"]
        for day, n, d_ in splits(r["cik"]) or []:
            if r["shares"]["end"] < day <= r["asof"]:
                sh *= n / d_
    x["mcap"] = px * sh if px and sh else None
    x["runway_m"] = x["burn_m"] = x["liquid"] = None
    stale = b and months_between(b["end"], r["asof"]) > 9
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
    x["debt"] = (r.get("debt") or {}).get("usd") or 0.0
    liquid = x["liquid"] or x["cash"]
    x["ev_x"] = (x["mcap"] + x["debt"] - liquid) / liquid if x["mcap"] and liquid else None
    t = r.get("trials")
    x["trials"] = t["other_trials"] if t else None
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
    x["raise"], x["raise_txt"] = raise_terms(x, r)
    x["gc"] = bool((r.get("going_concern") or {}).get("flag"))
    return x


def pending(kind, r, liq_ok, event, x):
    """Inputs still being collected for this episode's entry day (empty when complete)."""
    why = []
    if r.get("price") and x["px"] is None:
        why.append("actual share price not collected yet")
    if kind == "crash" and (not event or str(event.get("status", "")).startswith("error")):
        why.append("company announcement not read yet")
    if r.get("balance") and not liq_ok:
        why.append("cash items not read yet")
    rz = raises(r["cik"])
    if not rz or rz.get("checked", "") < r["asof"]:
        why.append("raise history not collected yet")
    sp, w = specialists(r["cik"], r["asof"])
    if w:
        why.append(w)
    si, w2 = short_interest(r["cik"], r["asof"])
    if w2:
        why.append(w2)
    x["spec"], x["si"] = sp, si
    x["short_pct"] = (si or {}).get("pct")
    return why


def verdict_rule(x):
    hard, soft = [], []
    if x["gc"]:
        hard.append("going-concern warning")
    if x["runway_m"] is None:
        hard.append("cash runway unknown")
    elif x["runway_m"] < GATE_RUNWAY:
        hard.append(f"runway under {GATE_RUNWAY} months")
    if x["px"] is not None and x["px"] < 1:
        hard.append("share price under $1")
    if x["raise"] in ("Costly", "Toxic") and x["runway_m"] is not None and x["runway_m"] < RAISE_MONTHS:
        hard.append(f"must raise within 2 years on {x['raise'].lower()} terms")
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
    if k in ("safety", "insider"):
        return x[k] or "n"
    if k == "raise":
        return {"Friendly": "g", "Costly": "r", "Toxic": "r"}.get(x["raise"], "n")
    if k == "specialists":
        return {"Bought": "g", "Sold": "r"}.get((x.get("spec") or {}).get("label"), "n")
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
    model["scores"] = sorted(score(model, keys, t) for t in train)
    return model


# ---------------------------------------------------------------- chances (logistic regression on categories)

def mbucket(v):
    return "N/A" if v is None else "<50M" if v < 5e7 else "50-300M" if v < 3e8 else "300M-2B" if v < 2e9 else "2B+"


def tokens(kind, x):
    rw, ev, si = x["runway_m"], x["ev_x"], x["short_pct"]
    t = ["runway:" + ("N/A" if rw is None else "none" if rw >= 999 else "<6" if rw < 6 else "6-12" if rw < 12 else "12-24" if rw < 24 else "24+"),
         "ev:" + ("N/A" if ev is None else "<0" if ev < 0 else "0-1" if ev < 1 else "1-2" if ev < 2 else "2+"),
         "mcap:" + mbucket(x["mcap"]), "sub1:" + str(x["px"] is not None and x["px"] < 1), "gc:" + str(x["gc"]),
         "ins:" + x["insider"], "raise:" + str(x["raise"]), "spec:" + str((x.get("spec") or {}).get("label")),
         "short:" + ("N/A" if si is None else "<5" if si < 5 else "5-15" if si < 15 else "15+"),
         "prog:" + ("N/A" if not x["trials"] else "1" if x["trials"] == 1 else "2-4" if x["trials"] < 5 else "5+")]
    if kind == "crash":
        t += ["safety:" + str(x["safety_txt"]), "cause:" + str(x["cause"])]
    return t


def logit(rows, target, l2=2.0):
    """L2-regularised logistic regression on 0/1 indicator features (Newton steps)."""
    vocab = sorted({t for r in rows for t in r["tok"]})
    idx = {t: i for i, t in enumerate(vocab)}
    X = np.zeros((len(rows), len(vocab) + 1))
    X[:, 0] = 1.0
    for i, r in enumerate(rows):
        for t in r["tok"]:
            X[i, idx[t] + 1] = 1.0
    y = np.array([1.0 if target(r) else 0.0 for r in rows])
    w = np.zeros(X.shape[1])
    reg = np.full(X.shape[1], l2)
    reg[0] = 0.0
    for _ in range(25):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        g = X.T @ (p - y) + reg * w
        H = (X * (p * (1 - p))[:, None]).T @ X + np.diag(reg + 1e-9)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-6:
            break
    return {"b": float(w[0]), "w": {t: float(w[i + 1]) for t, i in idx.items()}}


def prob(m, toks):
    z = m["b"] + sum(m["w"].get(t, 0.0) for t in toks)
    return float(1 / (1 + np.exp(-np.clip(z, -30, 30))))


# ---------------------------------------------------------------- cell text

def days_word(kind, d0, d):
    n = (date.fromisoformat(d) - date.fromisoformat(d0)).days
    return f"on {WORD[kind]} day" if n == 0 else f"{abs(n)} day{'s' if abs(n) > 1 else ''} {'after' if n > 0 else 'before'}"


def text(kind, k, x):
    if k == "safety":
        return x["safety_txt"] or "N/A"
    if k == "runway":
        v = x["runway_m"]
        return "N/A" if v is None else "No Cash Burn" if v >= 999 else f"{v:.0f} months"
    if k == "ev":
        v = x["ev_x"]
        return "N/A" if v is None else f"{v:.1f}×"
    if k == "programs":
        v = x["trials"]
        return "N/A" if not v else f"{v} other trial{'s' if v > 1 else ''}"
    if k == "raise":
        return x["raise_txt"]
    if k == "insider":
        if x["insider_net"] is None:
            return "N/A"
        side = "Bought" if x["insider_net"] > 0 else "Sold"
        return f"{side} {money(abs(x['insider_net']))}, {days_word(kind, x['date'], x['insider_trade']['date'])}"
    if k == "specialists":
        s = x.get("spec")
        if not s or s["label"] == "None":
            return "N/A"
        return f"{s['label']}, {s['funds']} fund{'s' if s['funds'] != 1 else ''}" if s["funds"] else f"{s['label']}, no fund left"
    if k == "short":
        v = x["short_pct"]
        return "N/A" if v is None else f"{v:.1f}%" if v < 10 else f"{v:.0f}%"


def bucket(k, x):
    if k == "safety":
        return x["safety_txt"] or "N/A"
    if k == "runway":
        v = x["runway_m"]
        return ("N/A" if v is None else "No cash burn" if v >= 999 else "Under 6 mo" if v < 6 else "6–12 mo" if v < 12
                else "12–24 mo" if v < 24 else "24+ mo")
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
    if k == "specialists":
        s = x.get("spec")
        return "N/A" if not s or s["label"] == "None" else s["label"]
    if k == "short":
        v = x["short_pct"]
        return "N/A" if v is None else "Under 5%" if v < 5 else "5–15%" if v < 15 else "15%+"


def note(k, x, model):
    r = x["raw"]
    eff = f"Past episodes like this: {model['points'][k].get(cat(model, x, k), 0.0) * 100:+.0f} pts vs similar biotechs"
    m = model["cuts"].get(k) if k in CONT else None
    if m:
        unit = {"runway": " months", "ev": "×", "programs": " other trials", "short": "%"}[k]
        eff += f" (split at {m['cut']:.1f}{unit})"
    if k == "runway":
        bits = []
        if r.get("balance"):
            bits.append(f"Liquid funds {money(r['balance']['cash'])} at {r['balance']['end']}")
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
        elif r.get("balance") and months_between(r["balance"]["end"], r["asof"]) > 9:
            bits.append("no balance sheet filed in the 9 months before entry")
        if x["gc"]:
            bits.append("Going-concern warning in the filing")
        return " · ".join(bits + [eff])
    if k == "ev" and x.get("mcap"):
        liq = x["liquid"] or x["cash"] or 0
        return f"(market value {money(x['mcap'])} + debt {money(x['debt'])} − liquid funds {money(liq)}) ÷ liquid funds · {eff}"
    if k == "raise":
        pf = r.get("public_float") or {}
        bits = [f"Public float {money(pf['usd'])} on the 10-K cover ({pf['as_of']})" if pf.get("usd") else "No public float on file"]
        q = r.get("financing_terms") or {}
        if q:
            bits.append("10-Q/10-K: “" + next(iter(q.values()))[:220] + "”")
        return " · ".join(bits)
    if k == "insider" and r.get("insiders"):
        t = x.get("insider_trade")
        who = f" · largest: {t['who']} {money(t['value'])} on {t['date']}" if t else ""
        return f"{len(r['insiders'].get('trades', []))} open-market trades, 90 days before to 30 days after{who} · the call uses trades filed by entry · {eff}"
    if k == "specialists" and x.get("spec") and x["spec"].get("quarter"):
        s = x["spec"]
        return f"13F filings for the quarter ended {s['quarter']}: {s['up']} added, {s['down']} cut, {s['funds']} holding"
    if k == "short" and x.get("si") and x["si"].get("settlement"):
        return f"FINRA short interest at settlement {x['si']['settlement']} ÷ shares outstanding · {eff}"
    if k == "programs" and r.get("trials"):
        ex = [e["drugs"][0] for e in r["trials"]["examples"][:2] if e["drugs"]]
        return (f"e.g. {'; '.join(dict.fromkeys(ex))} · " if ex else "") + eff
    return eff


# ---------------------------------------------------------------- run

def quarter_start(d):
    return f"{d[:4]}-{(int(d[5:7]) - 1) // 3 * 3 + 1:02d}-01"


def run(kind, ctrl):
    from liquidity import current
    keys = KEYS[kind]
    rows = []
    for c in episodes(kind):
        cid = f"{c['cik']}_{c['date']}"
        p = kdir(kind, "inputs") / f"{cid}.json"
        r = json.loads(p.read_text()) if p.exists() else None
        if not r or r.get("error") or r.get("version", 0) < 6 or r.get("asof") != c["end_date"]:
            rows.append({"id": cid, "tbu": {"id": cid, "verdict": "tbu", "why": ["inputs not collected yet"], "factors": {}}})
            continue
        lq = kdir(kind, "liquidity") / p.name
        liq_ok = current(lq, r)
        liq = json.loads(lq.read_text()) if liq_ok else None
        ev = None
        if kind == "crash":
            q = DATA / "crash" / "events" / p.name
            ev = json.loads(q.read_text()) if q.exists() else None
        x = features(kind, r, liq, ev)
        x["pending"] = pending(kind, r, liq_ok, ev, x)
        x["y"] = x["abs"] = None
        d365 = plus(r["asof"], 365)
        if d365 <= TODAY:
            a, _ = total_return(r["cik"], r["asof"], d365)
            cs = [min(v, 5.0) for v in (total_return(k, r["asof"], d365)[0] for k in (ctrl.get(cid) or {}).get("ciks", [])) if v is not None]
            if a is not None and cs:
                x["y"], x["abs"], x["d365"] = a - sum(cs) / len(cs), a, d365
        x["tok"] = tokens(kind, x)
        rows.append(x)
    feats = [x for x in rows if "tbu" not in x]
    known = sorted((x for x in feats if x["y"] is not None and not x["pending"]), key=lambda x: x["d365"])
    out_dir = kdir(kind, "scores")
    for old in out_dir.glob("*.json"):
        old.unlink()
    full = (fit(known, keys), logit(known, lambda t: t["abs"] >= 0.3), logit(known, lambda t: t["abs"] <= -0.8)) if len(known) >= MIN_TRAIN else None
    colours, chances, heldout, calib = {}, {}, [], []
    for x in rows:
        if "tbu" in x:
            (out_dir / f"{x['id']}.json").write_text(json.dumps(x["tbu"]))
            continue
        m0 = q0 = x["asof"][:7] + "-01"
        if m0 not in colours:                                  # colours: monthly refit on outcomes known by the 1st
            tr = [t for t in known if t["d365"] <= m0]
            colours[m0] = fit(tr, keys) if len(tr) >= MIN_TRAIN else None
        if q0 not in chances:                                  # chances: monthly refit, like the colours
            tr = [t for t in known if t["d365"] <= q0]
            chances[q0] = (logit(tr, lambda t: t["abs"] >= 0.3), logit(tr, lambda t: t["abs"] <= -0.8)) if len(tr) >= MIN_TRAIN else None
        ins = not (colours[m0] and chances[q0])
        if ins and not full:
            (out_dir / f"{x['id']}.json").write_text(json.dumps({"id": x["id"], "verdict": "tbu", "why": ["too little history to learn from"], "factors": {}}))
            continue
        model = colours[m0] or full[0]
        m_up, m_dn = chances[q0] or full[1:]
        verdict, why = verdict_rule(x)
        if x["pending"]:
            verdict, why = "tbu", x["pending"]

        def colour(k):
            if text(kind, k, x) == "N/A":
                return "n"
            if k == "insider":
                return "g" if x["insider_net"] > 0 else "r"
            if k == "safety":
                return cat(model, x, k)                     # the announcement's own wording sets the colour
            pts = model["points"][k].get(cat(model, x, k), 0.0)
            return "g" if pts >= 0.05 else "r" if pts <= -0.05 else "n"

        up = dn = None
        if verdict != "tbu":
            up, dn = round(prob(m_up, x["tok"]) * 100), round(prob(m_dn, x["tok"]) * 100)
        out = {"id": x["id"], "verdict": verdict, "why": why, "in_sample": ins, "trained_on": model["n"], "mcap": x["mcap"],
               "price": x["px"], "insider_net": x["insider_net"], "chance_up": up, "chance_down": dn, "cause": x["cause"],
               "factors": {k: [colour(k), text(kind, k, x), note(k, x, model), bucket(k, x)] for k in keys}}
        (out_dir / f"{x['id']}.json").write_text(json.dumps(out))
        if x["y"] is not None and verdict != "tbu":
            heldout.append((verdict, x["y"], x["abs"]))
            if not ins:
                calib.append((up, x["abs"] >= 0.3, dn, x["abs"] <= -0.8))
    n_tbu = sum(1 for x in rows if "tbu" in x or x.get("pending"))
    rep = [f"# Fit report: {kind}", f"episodes: {len(rows)} · still collecting (TBU): {n_tbu} · "
           f"with a full year and complete inputs: {len(known)}", ""]
    if full:
        rep += ["## Colours on all history", "", "| Cell | Cut-off | Points green / grey / red (pp vs similar biotechs) |", "|---|---|---|"]
        for k in keys:
            c = full[0]["cuts"].get(k) if k in CONT else None
            cs = f"{'≥' if c['higher_is_better'] else '<'} {c['cut']:.2f} is green" if c else ("categorical" if k not in CONT else "not enough data")
            pt = full[0]["points"][k]
            rep.append(f"| {k} | {cs} | {pt.get('g', 0) * 100:+.1f} / {pt.get('n', 0) * 100:+.1f} / {pt.get('r', 0) * 100:+.1f} |")
    if heldout:
        rep += ["", "## Backtest (complete inputs, a full year; mean caps any single return at +500%)", "",
                "| Call | n | median | mean | similar biotechs mean | beat them | up 30%+ | down 80%+ |", "|---|---|---|---|---|---|---|---|"]
        for v in ("overdone", "close", "justified"):
            g = [(y, a) for gg, y, a in heldout if gg == v]
            if g:
                ys, aa = [y for y, _ in g], [a for _, a in g]
                rep.append(f"| {v} | {len(g)} | {st.median(aa) * 100:+.0f}% | {sum(min(a, 5) for a in aa) / len(aa) * 100:+.0f}% | "
                           f"{sum(a - y for y, a in g) / len(g) * 100:+.0f}% | {sum(y > 0 for y in ys) / len(ys):.0%} "
                           f"| {sum(a >= .3 for a in aa) / len(aa):.0%} | {sum(a <= -.8 for a in aa) / len(aa):.0%} |")
    if calib:
        rep += ["", "## Chance check (walk-forward predictions only)", "", "| Predicted | n | up 30%+ happened | n | down 80%+ happened |", "|---|---|---|---|---|"]
        for lo, hi in ((0, 20), (20, 40), (40, 101)):
            a = [h for u, h, _, _ in calib if lo <= u < hi]
            b = [h for _, _, d, h in calib if lo <= d < hi]
            rep.append(f"| {lo}–{min(hi, 100)}% | {len(a)} | {sum(a) / len(a) if a else 0:.0%} | {len(b)} | {sum(b) / len(b) if b else 0:.0%} |")
    # the chance columns stay only while they pass their check: in every band with 20+ walk-forward predictions, what
    # happened is within 10 points of the band (0-20%, 20-40%, 40-100%)
    ok = {}
    for name, pi, hi_ in (("up", 0, 1), ("down", 2, 3)):
        good = True
        for lo, hi in ((0, 20), (20, 40), (40, 101)):
            h = [c[hi_] for c in calib if lo <= c[pi] < hi]
            if len(h) >= 20 and not (lo - 10 <= 100 * sum(h) / len(h) <= min(hi, 100) + 10):
                good = False
        ok[name] = good and len(calib) >= 50
    (DATA / kind / "calibration.json").write_text(json.dumps(ok))
    rep += ["", f"Chance columns shown: up 30%+ {'yes' if ok['up'] else 'no (check failed)'} · down 80%+ {'yes' if ok['down'] else 'no (check failed)'}"]
    (ROOT / "reports" / f"fit_{kind}.md").write_text("\n".join(rep) + "\n")
    print("\n".join(rep))


if __name__ == "__main__":
    c = controls.build()
    for kind in KINDS:
        run(kind, c)
