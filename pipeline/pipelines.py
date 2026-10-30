"""
One-off: a short, sourced pipeline description for a list of companies (pipeline_list.json), from public sources only.
  description  the company's own first sentence(s) about what it does, from Item 1 (Business) of its latest annual
               report (10-K or 20-F) on SEC EDGAR
  programs     its Phase 2/3 interventional trials as lead sponsor that are recruiting, active or about to start
               (ClinicalTrials.gov): drug, highest phase, conditions
Output: reports/pipelines.json
"""
import json, re
from collections import defaultdict
from urllib.parse import quote
from common import ROOT, filings, get, sec_text

STOP = {"inc", "corp", "corporation", "co", "ltd", "limited", "plc", "nv", "sa", "ag", "llc", "holdings", "holding",
        "group", "the", "de", "lp", "therapeutics", "pharmaceuticals", "biosciences", "bio", "biotherapeutics", "biopharma"}
DESC = re.compile(r"((?:We are|[A-Z][\w.,&' -]{1,60} is) (?:a|an) [^.]{10,320}?(?:compan(?:y|ies)|biotech\w*|biopharm\w*)[^.]{0,320}\.)(\s+[A-Z][^.]{20,320}\.)?")
ACTIVE = {"RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"}


def description(cik):
    for form, fd, acc, doc in filings(cik):
        if form in ("10-K", "20-F", "10-K405", "40-F"):
            t = sec_text(cik, acc, doc) or ""
            i = t.lower().find("item 1.", len(t) // 20)
            i = max(i, t.lower().find("overview"))
            m = DESC.search(re.sub(r"\s+", " ", t[max(0, i):i + 60000] if i > 0 else t[:200000]))
            if m:
                return {"text": (m.group(1) + (m.group(2) or "")).strip()[:600], "form": form, "filed": fd,
                        "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}"}
            return {"text": None, "form": form, "filed": fd}
    return None


def programs(name):
    words = [w for w in re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split() if w not in STOP]
    if not words:
        return []
    j = get("https://clinicaltrials.gov/api/v2/studies?pageSize=200&query.lead=" + quote(" ".join(words)) +
            "&fields=NCTId,LeadSponsorName,Phase,OverallStatus,InterventionName,Condition,StudyType") or {}
    drugs = defaultdict(lambda: {"phase": "", "conditions": set(), "trials": 0})
    for s in j.get("studies", []):
        p = s.get("protocolSection", {})
        lead = p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", "").lower()
        if not all(w in lead for w in words[:2]):
            continue
        st = p.get("statusModule", {}).get("overallStatus")
        ph = p.get("designModule", {}).get("phases") or []
        if st not in ACTIVE or not any(x in ("PHASE2", "PHASE3") for x in ph):
            continue
        top = "Phase 3" if "PHASE3" in ph else "Phase 2"
        for nm in (p.get("armsInterventionsModule", {}).get("interventions") or []):
            n = nm.get("name", "").strip()
            if not n or "placebo" in n.lower() or "standard of care" in n.lower():
                continue
            d = drugs[n]
            d["phase"] = max(d["phase"], top)
            d["trials"] += 1
            d["conditions"].update((p.get("conditionsModule", {}).get("conditions") or [])[:3])
    out = sorted(drugs.items(), key=lambda kv: (kv[1]["phase"], kv[1]["trials"]), reverse=True)[:4]
    return [{"drug": k, "phase": v["phase"], "trials": v["trials"], "conditions": sorted(v["conditions"])[:3]} for k, v in out]


if __name__ == "__main__":
    lst = json.load(open(ROOT / "pipeline" / "pipeline_list.json"))
    res = {}
    for r in lst:
        try:
            res[r["cik"]] = {"name": r["name"], "ticker": r["ticker"], "description": description(r["cik"]), "programs": programs(r["name"])}
        except Exception as e:
            res[r["cik"]] = {"name": r["name"], "ticker": r["ticker"], "error": str(e)[:200]}
        print(r["ticker"], bool((res[r["cik"]].get("description") or {}).get("text")), len(res[r["cik"]].get("programs") or []), flush=True)
    (ROOT / "reports" / "pipelines.json").write_text(json.dumps(res, indent=1))
