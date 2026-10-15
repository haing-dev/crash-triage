"""
Collect the raw inputs for each crash's factors, using only information public by day 30 after the news day
(the day returns start being measured), so there is no look-ahead.

  cash      latest balance sheet filed by day 30: cash + short/long-term marketable securities (SEC XBRL)
  burn      operating cash flow per month from the latest filed cash-flow statement (SEC XBRL)
  shares    shares outstanding from the latest cover page (SEC XBRL, dei)
  price     close on day 30 (first trading day on/after), un-adjusted for later reverse splits
  insiders  open-market buys (code P) and sales (code S) in Form 4s filed from news day to day 30
  trials    other Phase 2+ trials the company leads that were running on the news day (ClinicalTrials.gov)

Output: data/factors_raw/<cik>_<date>.json   (re-collected until day 30 has passed)

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
INVEST = ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
          "HeldToMaturitySecuritiesCurrent", "MarketableSecuritiesNoncurrent",
          "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent", "LongTermInvestments"]
BURN = ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
_facts, _subs = {}, {}


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
    for c in INVEST:
        v = [x for x in series(f, c) if x["end"] == end and x.get("filed", "9") <= asof and "start" not in x]
        if v:
            total += max(v, key=lambda x: x["filed"])["val"]
            parts.append(c)
    return {"end": end, "cash": total, "parts": parts, "accn": x0.get("accn")}


def burn(f, asof):
    """Operating cash flow per month from the latest cash-flow period filed by `asof` (negative = burning)."""
    cands = []
    for c in BURN:
        for x in series(f, c):
            if x.get("filed", "9") <= asof and x.get("start"):
                m = months(x["start"], x["end"])
                if 2.5 <= m <= 12.5:
                    cands.append((x["end"], m, x))
        if cands:
            break
    if not cands:
        return None
    end, m, x = max(cands, key=lambda t: (t[0], t[1]))
    return {"end": end, "months": round(m, 1), "per_month": x["val"] / m}


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
                votes.setdefault(f1, []).append(r)
    ev = sorted((d, sorted(r)[len(r) // 2]) for d, r in votes.items())
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
    buy = sell = 0.0
    buyers, n = set(), 0
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
            try:
                v = float(t.findtext("transactionAmounts/transactionShares/value") or 0) * \
                    float(t.findtext("transactionAmounts/transactionPricePerShare/value") or 0)
            except ValueError:
                continue
            if code == "P":
                buy += v
                buyers.add(who)
            elif code == "S":
                sell += v
    return {"form4s": n, "bought": round(buy), "sold": round(sell), "buyers": sorted(buyers)[:5]}


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
        day30 = (date.fromisoformat(c["date"]) + timedelta(days=30)).isoformat()
        asof = min(day30, TODAY)
        if out.exists():
            old = json.loads(out.read_text())
            if old.get("asof") == day30 and not old.get("error"):
                done += 1
                continue
        if n >= a.limit:
            break
        n += 1
        rec = {"id": cid, "cik": c["cik"], "name": c["name"], "date": c["date"], "asof": asof, "final": asof == day30}
        try:
            f = facts(c["cik"])
            ds, cs = closes(c["cik"])
            i = min(bisect_left(ds, asof), len(ds) - 1)
            if ds and ds[i] > asof and i > 0 and asof == TODAY:
                i -= 1
            rec["price"] = {"date": ds[i], "adj_close": cs[i]} if ds else None
            rec["balance"], rec["burn"], rec["shares"] = balance(f, asof), burn(f, asof), shares(f, asof)
            rec["insiders"] = insiders(c["cik"], c["date"], asof)
            ev = ROOT / "data" / "events" / f"{cid}.json"
            drug = ((json.loads(ev.read_text()).get("facts") or {}).get("drug")) if ev.exists() else None
            rec["drug"] = drug
            rec["trials"] = trials(c["name"], c["date"], drug)
        except Exception as e:
            rec["error"] = str(e)[:300]
        out.write_text(json.dumps(rec, indent=1))
        b, s = rec.get("balance") or {}, rec.get("insiders") or {}
        print(f"{c['date']} {c['ticker']:6s} cash={b.get('cash')} trials={(rec.get('trials') or {}).get('other_trials')} "
              f"buy={s.get('bought')} sell={s.get('sold')} {rec.get('error', '')[:80]}", flush=True)
    print(f"collected this run: {n} · already final: {done} · total crashes: {len(crashes)}")


if __name__ == "__main__":
    main()
