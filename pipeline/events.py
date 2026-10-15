"""
Crash triage only: find the company's own announcement for each crash (8-K/6-K filed from 3 days before to 6 days
after the news day, exhibit 99 press release first; or an offering prospectus) and have Claude read it into
structured facts. Every fact carries a verbatim quote checked against the filing; unverified facts are dropped.

Output: data/crash/events/<cik>_<date>.json (once per crash; errors are retried)

    python events.py --limit 60
"""
import argparse, json, re
from datetime import date, timedelta
from common import ask_claude, episodes, filings, get, kdir, norm, quoted, to_text

SCHEMA = """Return ONLY a JSON object, no prose, with exactly these keys:
{
 "related": true/false,            // does this document explain a large stock drop on the news day?
 "trigger": one of "efficacy miss", "safety", "regulatory rejection", "regulatory delay", "commercial miss",
            "financing / dilution", "partner exit", "other",
 "summary": "≤ 18 words, plain English, format: <what happened> · <phase or stage> · <drug> in <indication> · <market stage>",
 "phase": "Preclinical|Phase 1|Phase 1/2|Phase 2|Phase 2/3|Phase 3|Filed|Approved|n/a",
 "drug": "drug name or code, or n/a", "indication": "disease, or n/a",
 "market_stage": "not yet approved|filed for approval|on market|n/a",
 "primary_endpoint_met": true/false/null,
 "subgroup_or_secondary_positive": true/false/null,
 "safety": {"treated_patients": int|null, "serious_drug_related_patients": int|null, "deaths": int|null,
            "clinical_hold_or_trial_paused": true/false/null, "described_as_well_tolerated": true/false/null},
 "program_discontinued": true/false/null,
 "evidence": {"summary": "verbatim quote", "primary_endpoint_met": "verbatim quote or null",
              "safety": "verbatim quote or null", "program_discontinued": "verbatim quote or null"}
}
Rules: copy every evidence quote EXACTLY from the document (≤ 40 words, no paraphrase). Use null when the document
does not say. Do not use outside knowledge."""


def find_release(cik, d):
    # 8-Ks may be filed up to 4 business days after the news; offerings are announced in 424B prospectuses
    lo, hi = (date.fromisoformat(d) - timedelta(days=3)).isoformat(), (date.fromisoformat(d) + timedelta(days=6)).isoformat()
    cands = [(fd, acc, doc, form) for form, fd, acc, doc in filings(cik)
             if form in ("8-K", "6-K", "424B5", "424B4", "424B3") and lo <= fd <= hi]
    # nearest date first; on the same distance, an 8-K/6-K before a prospectus
    cands.sort(key=lambda c: (abs((date.fromisoformat(c[0]) - date.fromisoformat(d)).days), c[3].startswith("424")))
    cands = [c[:3] for c in cands]
    for fd, acc, prim in cands:
        a = acc.replace("-", "")
        idx = get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{a}/index.json") or {}
        names = [f["name"] for f in idx.get("directory", {}).get("item", [])]
        ex = [n for n in names if re.search(r"ex[-_]?99", n, re.I) and n.lower().endswith((".htm", ".html", ".txt"))]
        for n in ex + [prim]:
            b = get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{a}/{n}", raw=True)
            if b:
                t = to_text(b)
                if len(t) > 400:
                    return {"filing_date": fd, "accession": acc, "doc": n,
                            "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{a}/{n}", "text": t[:40000]}
    return None


def verify(facts, text):
    """Drop any fact whose evidence quote is not found word for word (after normalising) in the filing."""
    nt = norm(text)
    checked = {}
    for k, q in (facts.get("evidence") or {}).items():
        checked[k] = quoted(q, nt)
        if q and not checked[k]:
            if k == "safety":
                facts["safety"] = {kk: None for kk in (facts.get("safety") or {})}
            elif k in facts:
                facts[k] = None
    facts["evidence_verified"] = checked
    return facts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()
    out_dir = kdir("crash", "events")
    crashes = episodes("crash")
    done = n = 0
    for c in crashes:
        out = out_dir / f"{c['cik']}_{c['date']}.json"
        if out.exists() and not json.loads(out.read_text()).get("status", "").startswith("error"):
            done += 1
            continue
        if n >= a.limit:
            break
        n += 1
        rec = {"cik": c["cik"], "name": c["name"], "ticker": c["ticker"], "date": c["date"]}
        move = f"The stock fell {c['news_day_pct']}% that day and {c['fall_pct']}% in total over {c['days']} trading day(s)."
        try:
            rel = find_release(c["cik"], c["date"])
            if not rel:
                rec["status"] = "no filing found"
            else:
                prompt = f"Company: {c['name']}. News day: {c['date']}. {move}\nRead the SEC filing text on standard input.\n\n{SCHEMA}"
                facts = verify(ask_claude(prompt, rel["text"]), rel["text"])
                rec.update(status="read", source={k: rel[k] for k in ("filing_date", "accession", "doc", "url")}, facts=facts)
        except Exception as e:
            rec["status"] = f"error: {str(e)[:200]}"
        out.write_text(json.dumps(rec, indent=1))
        print(f"{c['date']} {c['ticker']:7s} {rec['status'][:60]}  {((rec.get('facts') or {}).get('summary') or '')[:90]}", flush=True)
    print(f"read this run: {n} · already done: {done} · total crashes: {len(crashes)}")


if __name__ == "__main__":
    main()
