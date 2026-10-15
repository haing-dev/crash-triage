"""
Collect the raw inputs for each crash's factors, using only information public by the trough (the lowest close of
the crash window), which is where the backtest buys, so there is no look-ahead.

  cash      latest balance sheet filed by the trough: cash + cash equivalents + marketable securities
            (short and long term), minus restricted cash (SEC XBRL)
  burn      operating cash outflow per month, trailing 12 months (smooths one-off receipts such as upfront
            payments) and latest quarter (SEC XBRL)
  guidance  the company's own runway statement in that 10-Q/10-K ("sufficient to fund operations into Q3 2027"),
            going-concern warnings, and at-the-market (ATM) equity programme mentions (filing text)
  shares    shares outstanding from the latest cover page (SEC XBRL, dei)
  price     close at the trough (split-adjusted; the actual price is recovered in fit.py)
  insiders  every open-market buy (P) and sale (S) in Form 4s from 90 days before the news day to 30 days after;
            fit.py only uses the ones filed by the trough for the verdict
  trials    other Phase 2+ trials the company leads that were running on the news day (ClinicalTrials.gov)

Output: data/factors_raw/<cik>_<date>.json   (re-collected until the 30-day insider window has passed)

    python factors_raw.py --limit 300 [--only CIK]
"""
import argparse, csv, json, re, time
import xml.etree.ElementTree as ET
from bisect import bisect_left
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote
from common import get

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "factors_raw"
TODAY = date.today().isoformat()
CASH = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"]
# readily liquid marketable securities: alternative tags for the same holdings, so take the largest of each group
# (adding them would double count); long-term "investments" (often equity stakes) are left out
INVEST_GROUPS = [["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                  "HeldToMaturitySecuritiesCurrent", "AvailableForSaleSecuritiesCurrent"],
                 ["MarketableSecuritiesNoncurrent", "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
                  "HeldToMaturitySecuritiesNoncurrent", "AvailableForSaleSecuritiesNoncurrent"]]
CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]
BURN = ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
RESTRICTED = ["RestrictedCash", "RestrictedCashCurrent", "RestrictedCashNoncurrent", "RestrictedCashAndCashEquivalentsAtCarryingValue",
              "RestrictedCashAndCashEquivalentsCurrent", "RestrictedCashAndCashEquivalentsNoncurrent"]
VERSION = 4
_facts, _subs, _docs = {}, {}, {}


def facts(cik):
    if cik not in _facts:
        _facts.clear()                                   # keep memory small: one company at a time
        _facts[cik] = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json") or {}
    return _facts[cik]


def series(f, concept, unit="USD", ns="us-gaap"):
    return (((f.get("facts") or {}).get(ns) or {}).get(concept) or {}).get("units", {}).get(unit, [])


def months(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days / 30.44


def balance(f, asof):
    """cash + investments on the latest balance sheet filed by `asof` (companies switch cash concepts over time)."""
    best = None
    for rank, c in enumerate(CASH):
        for x in series(f, c):
            if x.get("filed", "9") <= asof and "start" not in x:
                k = (x["end"], -rank, x["filed"])
                if best is None or k > best[0]:
                    best = (k, c, x)
    if not best:
        for x in series(f, "CashAndCashEquivalents", ns="ifrs-full"):
            if x.get("filed", "9") <= asof and (best is None or x["end"] > best[2]["end"]):
                best = ((x["end"],), "ifrs CashAndCashEquivalents", x)
    if not best:
        return None
    _, c0, x0 = best
    end, total, parts = x0["end"], x0["val"], [c0]
    if c0 == "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents":
        for c in RESTRICTED:                              # restricted cash can't fund operations
            v = [x for x in series(f, c) if x["end"] == end and x.get("filed", "9") <= asof and "start" not in x]
            if v:
                total -= max(v, key=lambda x: x["filed"])["val"]
                parts.append("minus " + c)
    for group in INVEST_GROUPS:
        best_v, best_c = 0, None
        for c in group:
            v = [x for x in series(f, c) if x["end"] == end and x.get("filed", "9") <= asof and "start" not in x]
            if v and max(v, key=lambda x: x["filed"])["val"] > best_v:
                best_v, best_c = max(v, key=lambda x: x["filed"])["val"], c
        if best_c:
            total += best_v
            parts.append(best_c)
    return {"end": end, "cash": total, "parts": parts, "accn": x0.get("accn")}


def burn(f, asof):
    """Operating cash flow per month (negative = burning): trailing 12 months and latest quarter, from the cash-flow
    statements filed by `asof`. 10-Qs report year-to-date figures, so quarters and TTM are built by differences."""
    vals = {}
    for c in BURN:
        for x in series(f, c):
            if x.get("filed", "9") <= asof and x.get("start"):
                k = (x["start"], x["end"])
                if k not in vals or x["filed"] > vals[k]["filed"]:
                    vals[k] = x
        if vals:
            break
    if not vals:
        return None
    m = lambda k: months(*k)
    E = max(e for _, e in vals)
    ytd = max((k for k in vals if k[1] == E and m(k) <= 12.5), key=m, default=None)
    if not ytd:
        return None
    mo = m(ytd)
    near = lambda a, b, tol=20: abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= tol
    ttm = None
    if mo >= 11.5:
        ttm = vals[ytd]["val"]
    else:
        fy = next((vals[k] for k in vals if 11.5 <= m(k) <= 12.5 and near(k[1], (date.fromisoformat(ytd[0]) - timedelta(days=1)).isoformat())), None)
        prev = next((vals[k] for k in vals if near(k[1], (date.fromisoformat(E) - timedelta(days=365)).isoformat()) and abs(m(k) - mo) < 0.6), None)
        if fy and prev:
            ttm = vals[ytd]["val"] + fy["val"] - prev["val"]
    q = None
    if mo <= 3.5:
        q = vals[ytd]["val"]
    else:
        before = next((vals[k] for k in vals if k[0] == ytd[0] and abs(m(k) - (mo - 3)) < 0.6), None)
        if before:
            q = vals[ytd]["val"] - before["val"]
    return {"end": E, "ytd_months": round(mo, 1), "ytd": vals[ytd]["val"], "accn": vals[ytd].get("accn"),
            "ttm_per_month": None if ttm is None else ttm / 12, "quarter_per_month": None if q is None else q / 3,
            "per_month": ttm / 12 if ttm is not None else vals[ytd]["val"] / mo}


def segments(f, concepts, asof, ns="us-gaap"):
    """Cash-flow facts are year-to-date; turn them into consecutive non-overlapping periods (quarters or halves):
    {(start, end): value}, using only filings made by `asof`."""
    vals = {}
    for c in concepts:
        for x in series(f, c, ns=ns):
            if x.get("filed", "9") <= asof and x.get("start"):
                k = (x["start"], x["end"])
                if k not in vals or x["filed"] > vals[k]["filed"]:
                    vals[k] = x
        if vals:
            break
    by_start = {}
    for (st, en), x in vals.items():
        by_start.setdefault(st, []).append((en, x["val"]))
    seg = {}
    for st, rows in by_start.items():
        rows.sort()
        prev_end, prev_val = None, 0.0
        for en, v in rows:
            a = st if prev_end is None else (date.fromisoformat(prev_end) + timedelta(days=1)).isoformat()
            if 1.5 <= months(a, en) <= 6.6:
                seg.setdefault((a, en), v - prev_val)
            prev_end, prev_val = en, v
    return seg


def last_six_months(seg, end):
    """Sum of the contiguous periods covering the six months to `end`."""
    total, cur, covered, used = 0.0, end, 0.0, []
    while covered < 5.5:
        k = next((k for k in seg if k[1] == cur), None)
        if not k:
            return None
        total += seg[k]
        covered += months(k[0], k[1])
        used.append(k)
        cur = (date.fromisoformat(k[0]) - timedelta(days=1)).isoformat()
    return total if covered <= 7 else None, used


def burn6(f, asof):
    """Latest six months of net cash used in operations + capital expenditure (both as positive cash out)."""
    ocf = segments(f, BURN, asof) or segments(f, ["CashFlowsFromUsedInOperatingActivities"], asof, ns="ifrs-full")
    if not ocf:
        return None
    end = max(k[1] for k in ocf)
    r = last_six_months(ocf, end)
    if not r or r[0] is None:
        return None
    op6, used = r
    cap = segments(f, CAPEX, asof) or segments(f, ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"], asof, ns="ifrs-full")
    capr = last_six_months(cap, end) if cap else None
    capex6 = capr[0] if capr and capr[0] is not None else None
    if capex6 is None:                                    # capex only in a longer period: pro-rate it to six months
        longest = max((x for x in series(f, CAPEX[0]) + series(f, CAPEX[1]) if x.get("start") and x["end"] == end
                       and x.get("filed", "9") <= asof), key=lambda x: months(x["start"], x["end"]), default=None)
        capex6 = longest["val"] * 6 / months(longest["start"], longest["end"]) if longest else 0.0
    return {"end": end, "start": used[-1][0], "cash_used_ops": -op6, "capex": capex6, "periods": len(used)}


MONTH = {"first": 1, "second": 4, "third": 7, "fourth": 10, "q1": 1, "q2": 4, "q3": 7, "q4": 10, "1q": 1, "2q": 4, "3q": 7, "4q": 10}
GUIDE = re.compile(r"(?:(?:sufficient|enough|adequate|able|enable us|allow us|enables us|allows us)\s+to\s+(?:fund|support|finance|meet)"
                   r"|(?:cash|capital|financial)\s+runway|to fund (?:our |its )?(?:operations|operating expenses|operating plan|planned operations|current operating plan))"
                   r"[^.]{0,220}?"
                   r"\b(into|through|until|to|beyond|at least into|at least through|well into)\s+(?:the\s+)?"
                   r"(?:(first|second|third|fourth)\s+(?:calendar\s+)?quarter\s+of\s+|(q[1-4]|[1-4]q)\s+|"
                   r"(first|second)\s+half\s+of\s+|(mid|middle of|end of|beginning of|early|late)[-\s]+(?:calendar\s+)?)?(20\d\d)", re.I)
TWELVE = re.compile(r"(?:sufficient|enough|adequate) to (?:fund|support|finance|meet)[^.]{0,200}?(?:at least\s+)?(?:the\s+)?next\s+(?:12|twelve)\s+months", re.I)
GOING = re.compile(r"substantial doubt (?:about|regarding|as to|concerning) (?:our|the company.s|the company’s|its) ability to continue as a going concern", re.I)
HYPO = re.compile(r"\b(?:could|may|might|would|if|unless|whether|cannot assure|no assurance|in the event)\b", re.I)
NEG = re.compile(r"\b(?:need|needs|require|requires|additional (?:capital|funding|financing)|not be sufficient|insufficient|not sufficient)\b", re.I)
ATM = re.compile(r"at[- ]the[- ]market|\bATM\b|sales agreement", re.I)
ATM_LEFT = re.compile(r"\$\s?([\d,.]+)\s*(million|billion)?[^.]{0,120}?remain(?:ed|s|ing)?\s+available"
                      r"|remaining (?:available |aggregate )?(?:capacity|amount|offering price)[^.$]{0,60}\$\s?([\d,.]+)\s*(million|billion)?", re.I)


def guide_date(mt):
    word, qtr, qn, half, edge, yr = mt.groups()
    y = int(yr)
    through = word.lower() in ("through", "until", "to", "at least through", "beyond")
    if qtr or qn:
        mth = MONTH[(qtr or qn).lower()] + (2 if through else 0)
    elif half:
        mth = (1 if half.lower() == "first" else 7) + (5 if through else 0)
    elif edge:
        e = edge.lower()
        mth = 7 if e.startswith("mid") else 12 if e in ("end of", "late") else 1 if e in ("beginning of", "early") else 7
    else:
        mth = 12 if through else 1
    return f"{y}-{mth:02d}-15"


def doc_text(cik, accn):
    """Plain text of a filing's main document (cached per filing)."""
    if not accn:
        return None
    if accn not in _docs:
        _docs.clear()
        doc = next((d for fm, fd, a, d in subs(cik) if a == accn), None)
        t = None
        if doc:
            b = get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{doc.split('/')[-1]}", raw=True)
            if b:
                t = re.sub(r"<[^>]+>", " ", b.decode("utf-8", "replace"))
                t = re.sub(r"&#160;|&nbsp;|&#xa0;", " ", t)
                t = re.sub(r"&#8217;|&rsquo;", "'", t)
                t = re.sub(r"\s+", " ", t)
        _docs[accn] = t
    return _docs[accn]


def filing_notes(cik, accn, filed):
    t = doc_text(cik, accn)
    if not t:
        return None
    out = {"accn": accn, "guided_until": None, "guidance": None, "twelve_months": False, "going_concern": False,
           "atm": False, "atm_remaining": None, "atm_quote": None}
    lim = (date.fromisoformat(filed) + timedelta(days=6 * 365)).isoformat()
    def sentence(mt):
        a0 = t.rfind(". ", 0, mt.start()) + 2
        return t[a0:mt.end()]
    g = [mt for mt in GUIDE.finditer(t) if guide_date(mt) <= lim and not NEG.search(sentence(mt))]
    if g:
        best = max(g, key=lambda mt: guide_date(mt))          # several statements: the company's longest horizon
        out["guided_until"] = guide_date(best)
        st = max(0, best.start() - 40)
        out["guidance"] = t[st:best.end() + 5].strip()[:300]
    elif TWELVE.search(t):
        out["twelve_months"] = True
        out["guidance"] = TWELVE.search(t).group(0)[:300]
    # a real going-concern conclusion, not the hypothetical risk-factor wording ("could raise substantial doubt")
    for gc in GOING.finditer(t):
        a0 = t.rfind(". ", 0, gc.start()) + 2
        lead, after = t[a0:gc.start()], t[gc.end():gc.end() + 200].lower()
        if HYPO.search(lead) or any(w in (lead.lower() + after) for w in ("alleviat", "no longer", "do not believe", "does not exist", "not have substantial")):
            continue
        out["going_concern"], out["going_concern_quote"] = True, t[a0:gc.end()][:300]
        break
    a = ATM.search(t)
    if a:
        out["atm"] = True
        out["atm_quote"] = t[max(0, a.start() - 150):a.end() + 250].strip()[:400]
        for mt in ATM_LEFT.finditer(t):
            ctx = t[max(0, mt.start() - 300):mt.end()].lower()
            if "at-the-market" in ctx or "at the market" in ctx or "sales agreement" in ctx or "atm" in ctx:
                num, unit = (mt.group(1), mt.group(2)) if mt.group(1) else (mt.group(3), mt.group(4))
                try:
                    v = float(num.replace(",", "").rstrip(".")) * (1e9 if (unit or "").lower() == "billion" else 1e6 if unit else 1)
                except ValueError:
                    continue
                out["atm_remaining"] = v
                break
    return out


def split_events(f):
    """Reverse/forward splits, found where a later filing restates an earlier period's share count by a big ratio.
    Returns [(filed_date_of_restating_filing, ratio)] with ratio = old shares / new shares (>1 for a reverse split)."""
    by_period = {}
    for c in ("WeightedAverageNumberOfSharesOutstandingBasic", "WeightedAverageNumberOfShareOutstandingBasicAndDiluted"):
        for x in series(f, c, "shares"):
            if x.get("val") and x.get("filed"):
                by_period.setdefault((c, x.get("start"), x["end"]), []).append((x["filed"], x["val"]))
    votes = {}
    for vals in by_period.values():
        vals.sort()
        for (f0, v0), (f1, v1) in zip(vals, vals[1:]):
            r = v0 / v1
            if f1 > f0 and (r > 1.8 or r < 0.55):
                votes.setdefault(f1, []).append((f0, r))
    ev = []
    for d, v in votes.items():                            # compare only with the most recent earlier report,
        last = max(f0 for f0, _ in v)                     # so two splits in a row aren't multiplied twice
        rs = sorted(r for f0, r in v if f0 == last)
        ev.append((d, rs[len(rs) // 2]))
    ev.sort()
    out = []
    for d, r in ev:                                       # one split restated in several filings: keep the first
        if out and abs(r / out[-1][1] - 1) < 0.1 and (date.fromisoformat(d) - date.fromisoformat(out[-1][0])).days < 400:
            continue
        out.append((d, r))
    return out


def shares(f, asof):
    """Shares on the latest cover page filed by `asof`, and the split factor applied to prices since then."""
    s = sorted((x for x in series(f, "EntityCommonStockSharesOutstanding", "shares", "dei") if x.get("val")),
               key=lambda x: (x["end"], x.get("filed", "")))
    known = [x for x in s if x.get("filed", "9") <= asof]
    if not known:
        return None
    cur = known[-1]
    # Prices are split-adjusted to today's basis: divide the adjusted price by the product of later splits.
    ev = [(d, r) for d, r in split_events(f) if d > cur["filed"]]
    factor = 1.0
    for _, r in ev:
        factor *= r
    same = [x["val"] for x in known if x["end"] == cur["end"] and x.get("accn") == cur.get("accn")]
    return {"end": cur["end"], "shares": sum(same) if len(same) > 1 else cur["val"], "split_factor": round(factor, 3),
            "splits_after": [[d, round(r, 2)] for d, r in ev]}


def subs(cik):
    """All filings for the company: (form, filingDate, accession, primaryDocument)."""
    if cik not in _subs:
        _subs.clear()
        s = get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json") or {}
        pages = [s.get("filings", {}).get("recent", {})]
        for fl in s.get("filings", {}).get("files", []):
            pages.append(get(f"https://data.sec.gov/submissions/{fl['name']}") or {})
        rows = []
        for r in pages:
            rows += list(zip(r.get("form", []), r.get("filingDate", []), r.get("accessionNumber", []), r.get("primaryDocument", [])))
        _subs[cik] = rows
    return _subs[cik]


def insiders(cik, d0, d1):
    """Every open-market buy (P) and sale (S) in Form 4s filed between d0 and d1."""
    trades, n = [], 0
    for form, fd, acc, doc in subs(cik):
        if form != "4" or not (d0 <= fd <= d1):
            continue
        n += 1
        b = get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc.split('/')[-1]}", raw=True)
        if not b:
            continue
        try:
            x = ET.fromstring(b)
        except ET.ParseError:
            continue
        if (x.findtext("issuer/issuerCik") or "").lstrip("0") != str(int(cik)):
            continue                                     # a Form 4 about another company
        who = x.findtext("reportingOwner/reportingOwnerId/rptOwnerName") or "?"
        for t in x.iter("nonDerivativeTransaction"):
            code = t.findtext("transactionCoding/transactionCode")
            if code not in ("P", "S"):
                continue
            try:
                v = float(t.findtext("transactionAmounts/transactionShares/value") or 0) * \
                    float(t.findtext("transactionAmounts/transactionPricePerShare/value") or 0)
            except ValueError:
                continue
            td = (t.findtext("transactionDate/value") or fd)[:10]
            trades.append({"date": td, "filed": fd, "code": code, "value": round(v), "who": who})
    return {"form4s": n, "from": d0, "to": d1, "trades": trades}


STOP = {"inc", "corp", "corporation", "co", "ltd", "limited", "plc", "nv", "n", "v", "sa", "ag", "ab", "as", "llc",
        "holdings", "holding", "group", "the", "de", "lp"}


def name_tokens(s):
    return [w for w in re.sub(r"[^a-z0-9 ]+", " ", s.lower()).split() if w not in STOP]


def trials(name, d, drug):
    q = " ".join(name_tokens(name))
    if not q:
        return None
    url = ("https://clinicaltrials.gov/api/v2/studies?pageSize=200&query.lead=" + quote(q) +
           "&fields=NCTId,LeadSponsorName,Phase,StartDate,PrimaryCompletionDate,CompletionDate,OverallStatus,InterventionName")
    j = get(url)
    if j is None:
        return None
    want = set(name_tokens(name))
    drug_t = set(name_tokens(drug or "")) - {"n", "a"}
    other, crashed = [], 0
    for s in j.get("studies", []):
        p = s.get("protocolSection", {})
        lead = name_tokens(p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", ""))
        if not want <= set(lead):
            continue
        phases = p.get("designModule", {}).get("phases") or []
        if not any(ph in ("PHASE2", "PHASE3") for ph in phases):
            continue
        st = p.get("statusModule", {})
        start = (st.get("startDateStruct") or {}).get("date", "")
        end = (st.get("primaryCompletionDateStruct") or st.get("completionDateStruct") or {}).get("date", "")
        if not start or start > d or (end and end < d[:len(end)]):
            continue
        names = [i.get("name", "") for i in p.get("armsInterventionsModule", {}).get("interventions", [])]
        if drug_t and any(drug_t & set(name_tokens(nm)) for nm in names):
            crashed += 1
            continue
        other.append({"nct": p["identificationModule"]["nctId"], "drugs": [n for n in names if "placebo" not in n.lower()][:3]})
    return {"query": q, "other_trials": len(other), "crashed_drug_trials": crashed, "examples": other[:5]}


def closes(cik):
    p = ROOT / "data" / "prices" / f"{cik}.csv"
    rows = [(r["date"], float(r["close"])) for r in csv.DictReader(p.open()) if r["close"]] if p.exists() else []
    return [d for d, _ in rows], [c for _, c in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--only")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    crashes = sorted(csv.DictReader((ROOT / "data" / "crashes.csv").open()), key=lambda c: c["cik"])
    n = done = 0
    for c in crashes:
        if a.only and c["cik"] != a.only:
            continue
        cid = f"{c['cik']}_{c['date']}"
        out = OUT / f"{cid}.json"
        trough = c["end_date"]
        win0 = (date.fromisoformat(c["date"]) - timedelta(days=90)).isoformat()
        win1 = (date.fromisoformat(c["date"]) + timedelta(days=30)).isoformat()
        final = win1 <= TODAY
        if out.exists():
            old = json.loads(out.read_text())
            if old.get("version") == VERSION and old.get("final") and not old.get("error"):
                done += 1
                continue
        if n >= a.limit:
            break
        n += 1
        rec = {"id": cid, "cik": c["cik"], "name": c["name"], "date": c["date"], "asof": trough, "final": final, "version": VERSION}
        try:
            f = facts(c["cik"])
            ds, cs = closes(c["cik"])
            i = bisect_left(ds, trough)
            rec["price"] = {"date": ds[i], "adj_close": cs[i]} if ds and i < len(ds) else None
            rec["balance"], rec["burn"], rec["shares"] = balance(f, trough), burn(f, trough), shares(f, trough)
            rec["burn6"] = burn6(f, trough)
            src = (rec["balance"] or {}).get("accn") or (rec["burn"] or {}).get("accn")
            rec["filing"] = filing_notes(c["cik"], src, trough) if src else None
            rec["insiders"] = insiders(c["cik"], win0, min(win1, TODAY))
            ev = ROOT / "data" / "events" / f"{cid}.json"
            drug = ((json.loads(ev.read_text()).get("facts") or {}).get("drug")) if ev.exists() else None
            rec["drug"] = drug
            rec["trials"] = trials(c["name"], c["date"], drug)
        except Exception as e:
            rec["error"] = str(e)[:300]
        out.write_text(json.dumps(rec, indent=1))
        b, fl = rec.get("balance") or {}, rec.get("filing") or {}
        print(f"{c['date']} {c['ticker']:6s} cash={b.get('cash')} guided={fl.get('guided_until')} atm={fl.get('atm')} "
              f"trades={len((rec.get('insiders') or {}).get('trades', []))} {rec.get('error', '')[:80]}", flush=True)
    print(f"collected this run: {n} · already final: {done} · total crashes: {len(crashes)}")


if __name__ == "__main__":
    main()
