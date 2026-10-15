"""
For each crash, find the company's own announcement (SEC 8-K filed around the crash date, exhibit 99.x press
release), and have Claude read it into structured facts. Every fact must carry a verbatim quote that is checked
against the filing text; facts whose quote doesn't match are dropped.

Output: data/events/<cik>_<date>.json   (resumable; already-read crashes are skipped)

    python events.py --limit 40
"""
import argparse, csv, html, json, re, shutil, subprocess, tempfile, time
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from common import get

ROOT = Path(__file__).resolve().parent.parent
EDIR = ROOT / "data" / "events"
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


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, t, a):
        if t in ("script", "style"):
            self.skip += 1
        if t in ("p", "div", "br", "tr", "li", "h1", "h2", "h3"):
            self.out.append("\n")

    def handle_endtag(self, t):
        if t in ("script", "style"):
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip:
            self.out.append(d)


def to_text(b):
    p = Text()
    p.feed(b.decode("utf-8", "replace"))
    t = html.unescape("".join(p.out))
    return re.sub(r"[ \t\xa0]+", " ", re.sub(r"\n\s*\n+", "\n\n", t)).strip()


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%$.]+", " ", s.lower().replace("’", "'"))).strip()


def find_release(cik, d):
    s = get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    r = s["filings"]["recent"]
    lo, hi = (date.fromisoformat(d) - timedelta(days=3)).isoformat(), (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    cands = [(r["filingDate"][i], r["accessionNumber"][i], r["primaryDocument"][i]) for i in range(len(r["form"]))
             if r["form"][i] in ("8-K", "6-K") and lo <= r["filingDate"][i] <= hi]
    cands.sort(key=lambda c: abs((date.fromisoformat(c[0]) - date.fromisoformat(d)).days))
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


def ask_claude(text, name, d, move):
    exe = shutil.which("claude")
    prompt = (f"Company: {name}. News day: {d}. {move}\n"
              f"Read the SEC filing text on standard input.\n\n{SCHEMA}")
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([exe, "-p", prompt, "--model", "sonnet", "--effort", "medium", "--tools", "",
                            "--strict-mcp-config", "--no-session-persistence", "--output-format", "text"],
                           input=text, capture_output=True, text=True, timeout=600, cwd=td)
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if r.returncode != 0 or not m:
        raise RuntimeError((r.stderr or r.stdout)[:300])
    return json.loads(m.group(0))


def verify(facts, text):
    """Drop any fact whose evidence quote is not found word-for-word (after normalising) in the filing."""
    nt = norm(text)
    ev = facts.get("evidence") or {}
    checked = {}
    for k, q in ev.items():
        ok = bool(q) and all(norm(f) in nt for f in re.split(r"…|\.\.\.", q) if len(norm(f).split()) >= 3)
        checked[k] = ok
        if q and not ok:
            if k == "safety":
                facts["safety"] = {kk: None for kk in (facts.get("safety") or {})}
            elif k in facts:
                facts[k] = None
    facts["evidence_verified"] = checked
    return facts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()
    EDIR.mkdir(parents=True, exist_ok=True)
    crashes = list(csv.DictReader((ROOT / "data" / "crashes.csv").open()))
    done = n = 0
    for c in crashes:
        out = EDIR / f"{c['cik']}_{c['date']}.json"
        if out.exists() and not json.loads(out.read_text()).get("status", "").startswith("error"):
            done += 1
            continue
        if n >= a.limit:
            break
        n += 1
        rec = {"cik": c["cik"], "name": c["name"], "ticker": c["ticker"], "date": c["date"], "fall_pct": c["fall_pct"],
               "days": c["days"], "news_day_pct": c["news_day_pct"]}
        move = (f"The stock fell {c['news_day_pct']}% that day and {c['fall_pct']}% in total over {c['days']} "
                f"trading day(s).")
        try:
            rel = find_release(c["cik"], c["date"])
            if not rel:
                rec["status"] = "no filing found"
            else:
                facts = verify(ask_claude(rel["text"], c["name"], c["date"], move), rel["text"])
                rec.update(status="read", source={k: rel[k] for k in ("filing_date", "accession", "doc", "url")}, facts=facts)
        except Exception as e:
            rec["status"] = f"error: {str(e)[:200]}"
        out.write_text(json.dumps(rec, indent=1))
        print(f"{c['date']} {c['ticker']:7s} {rec['status'][:60]}  {((rec.get('facts') or {}).get('summary') or '')[:90]}", flush=True)
    print(f"read this run: {n} · already done: {done} · total crashes: {len(crashes)}")


if __name__ == "__main__":
    main()
