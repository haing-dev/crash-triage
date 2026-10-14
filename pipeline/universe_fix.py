"""Second pass for universe rows without a ticker or price source: read the ticker from the last few 10-Q/10-K
cover pages (inline XBRL in the main document as well as the XBRL instance), then re-match to Tiingo."""
import csv, io, re, zipfile
from pathlib import Path
from common import get
import universe_lib as L

ROOT = Path(__file__).resolve().parent.parent
path = ROOT / "data" / "universe.csv"
rows = list(csv.DictReader(path.open()))
tl = L.tiingo_list()
fixed = 0
for r in rows:
    if r["price_source"]:
        continue
    s = get(f"https://data.sec.gov/submissions/CIK{int(r['cik']):010d}.json")
    rec = s["filings"]["recent"]
    per = sorted(((rec["filingDate"][k], rec["accessionNumber"][k], rec["primaryDocument"][k]) for k in range(len(rec["form"]))
                  if rec["form"][k] in ("10-K", "10-Q", "10-K/A", "10-Q/A")), reverse=True)[:4]
    tick = ""
    for fd, acc, prim in per:
        a = acc.replace("-", "")
        idx = get(f"https://www.sec.gov/Archives/edgar/data/{int(r['cik'])}/{a}/index.json") or {}
        names = [f["name"] for f in idx.get("directory", {}).get("item", [])]
        for n in [x for x in names if x.endswith("_htm.xml")] + [prim]:
            b = get(f"https://www.sec.gov/Archives/edgar/data/{int(r['cik'])}/{a}/{n}", raw=True) or b""
            m = re.findall(rb"(?:<dei:TradingSymbol[^>]*>|name=\"dei:TradingSymbol\"[^>]*>)\s*(?:<[^>]+>\s*)*([A-Za-z.\-]{1,6})\s*<", b)
            if m:
                tick = m[0].decode().upper()
                break
        if tick:
            break
    if not tick:
        continue
    tt, tr = L.tiingo_match(tl, tick, r["first_period"], r["last_period"])
    r["ticker"] = tick
    if tr:
        r.update(status="stopped trading" if tr["endDate"] < "2026-09" else "listed (via Tiingo)", price_source="tiingo",
                 tiingo_ticker=tt, tiingo_start=tr["startDate"], tiingo_end=tr["endDate"])
        fixed += 1
    else:
        r["status"] = "ticker found · no price source"
    print(f"{r['name'][:40]:40s} {tick:6s} {r['status']}", flush=True)
with path.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"fixed {fixed}; still without a price source: {sum(1 for r in rows if not r['price_source'])}")
