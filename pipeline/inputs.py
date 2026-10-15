"""
Collect each episode's factor inputs from public sources, using only what was public by the entry day (the crash
trough, or the day a slide crossed -30%), which is where the backtest buys: no look-ahead.

  liquid funds  latest balance sheet filed by entry: unrestricted cash & equivalents + readily liquid marketable
                securities (SEC XBRL; restricted cash removed; long-term investments left out)
  burn6         latest six months of net cash used in operations + capital expenditure (SEC XBRL year-to-date
                figures turned into quarters/halves)
  shares        shares outstanding on the latest cover page (SEC XBRL, dei) -> market value with the actual price
  going concern a real going-concern conclusion in that 10-Q/10-K (not the hypothetical risk-factor wording)
  insiders      every open-market buy (P) and sale (S) in Form 4s from 90 days before the episode date to 30 after
  trials        other Phase 2+ trials the company leads that were running on the episode date (ClinicalTrials.gov)

Output: data/<kind>/inputs/<cik>_<date>.json (re-collected until the 30-day insider window has passed)

    python inputs.py --kind crash --limit 2000
"""
import argparse, json, re
import xml.etree.ElementTree as ET
from bisect import bisect_left
from datetime import date, timedelta
from functools import lru_cache
from urllib.parse import quote
from common import closes, episodes, filings, get, kdir, sec_text, sec_url, KINDS

TODAY = date.today().isoformat()
VERSION = 5
CASH = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"]
RESTRICTED = ["RestrictedCash", "RestrictedCashCurrent", "RestrictedCashNoncurrent", "RestrictedCashAndCashEquivalentsAtCarryingValue",
              "RestrictedCashAndCashEquivalentsCurrent", "RestrictedCashAndCashEquivalentsNoncurrent"]
# readily liquid marketable securities: alternative tags for the same holdings, so take the largest of each group
INVEST_GROUPS = [["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                  "HeldToMaturitySecuritiesCurrent", "AvailableForSaleSecuritiesCurrent"],
                 ["MarketableSecuritiesNoncurrent", "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
                  "HeldToMaturitySecuritiesNoncurrent", "AvailableForSaleSecuritiesNoncurrent"]]
BURN = ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]
GOING = re.compile(r"substantial doubt (?:about|regarding|as to|concerning) (?:our|the company.s|the company’s|its) ability to continue as a going concern", re.I)
HYPO = re.compile(r"\b(?:could|may|might|would|if|unless|whether|cannot assure|no assurance|in the event)\b", re.I)


@lru_cache(maxsize=2)
def facts(cik):
    return get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json") or {}


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


def shares(f, asof):
    """Shares outstanding on the latest cover page filed by `asof` (several share classes on one date are added)."""
    s = sorted((x for x in series(f, "EntityCommonStockSharesOutstanding", "shares", "dei") if x.get("val")),
               key=lambda x: (x["end"], x.get("filed", "")))
    known = [x for x in s if x.get("filed", "9") <= asof]
    if not known:
        return None
    cur = known[-1]
    same = [x["val"] for x in known if x["end"] == cur["end"] and x.get("accn") == cur.get("accn")]
    return {"end": cur["end"], "shares": sum(same) if len(same) > 1 else cur["val"]}


def going_concern(cik, accn):
    """A real going-concern conclusion in the filing, not the hypothetical risk-factor wording."""
    t = sec_text(cik, accn) if accn else None
    if not t:
        return None
    t = re.sub(r"\s+", " ", t)
    for gc in GOING.finditer(t):
        a0 = t.rfind(". ", 0, gc.start()) + 2
        lead, after = t[a0:gc.start()], t[gc.end():gc.end() + 200].lower()
        if HYPO.search(lead) or any(w in lead.lower() + after for w in ("alleviat", "no longer", "do not believe", "does not exist", "not have substantial")):
            continue
        return {"flag": True, "quote": t[a0:gc.end()][:300]}
    return {"flag": False}


def insiders(cik, d0, d1):
    """Every open-market buy (P) and sale (S) in Form 4s filed between d0 and d1."""
    trades, n = [], 0
    for form, fd, acc, doc in filings(cik):
        if form != "4" or not (d0 <= fd <= d1):
            continue
        n += 1
        b = get(sec_url(cik, acc, doc), raw=True)
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


@lru_cache(maxsize=4)
def trials_query(q):
    return get("https://clinicaltrials.gov/api/v2/studies?pageSize=200&query.lead=" + quote(q) +
               "&fields=NCTId,LeadSponsorName,Phase,StartDate,PrimaryCompletionDate,CompletionDate,OverallStatus,InterventionName")


def trials(name, d, drug):
    q = " ".join(name_tokens(name))
    if not q:
        return None
    j = trials_query(q)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=KINDS, required=True)
    ap.add_argument("--limit", type=int, default=2000)
    a = ap.parse_args()
    out_dir = kdir(a.kind, "inputs")
    eps = sorted(episodes(a.kind), key=lambda c: c["cik"])          # company by company: XBRL/filings stay cached
    n = done = 0
    for c in eps:
        cid = f"{c['cik']}_{c['date']}"
        out = out_dir / f"{cid}.json"
        entry = c["end_date"]
        win0 = (date.fromisoformat(c["date"]) - timedelta(days=90)).isoformat()
        win1 = (date.fromisoformat(c["date"]) + timedelta(days=30)).isoformat()
        if out.exists():
            old = json.loads(out.read_text())
            if old.get("version") == VERSION and old.get("final") and not old.get("error"):
                done += 1
                continue
        if n >= a.limit:
            break
        n += 1
        rec = {"id": cid, "kind": a.kind, "cik": c["cik"], "name": c["name"], "date": c["date"], "asof": entry,
               "final": win1 <= TODAY, "version": VERSION}
        try:
            f = facts(c["cik"])
            ds, cs = closes(c["cik"])
            i = bisect_left(ds, entry)
            rec["price"] = {"date": ds[i], "adj_close": cs[i]} if i < len(ds) else None
            rec["balance"], rec["burn6"], rec["shares"] = balance(f, entry), burn6(f, entry), shares(f, entry)
            rec["going_concern"] = going_concern(c["cik"], (rec["balance"] or {}).get("accn"))
            rec["insiders"] = insiders(c["cik"], win0, min(win1, TODAY))
            ev = kdir("crash", "events") / f"{cid}.json"
            drug = ((json.loads(ev.read_text()).get("facts") or {}).get("drug")) if a.kind == "crash" and ev.exists() else None
            rec["drug"] = drug
            rec["trials"] = trials(c["name"], c["date"], drug)
        except Exception as e:
            rec["error"] = str(e)[:300]
        out.write_text(json.dumps(rec, indent=1))
        b = rec.get("balance") or {}
        print(f"{a.kind} {c['date']} {c['ticker']:6s} cash={b.get('cash')} trades={len((rec.get('insiders') or {}).get('trades', []))} "
              f"{rec.get('error', '')[:80]}", flush=True)
    print(f"{a.kind}: collected this run {n} · already final {done} · episodes {len(eps)}")


if __name__ == "__main__":
    main()
