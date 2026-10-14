"""
Build the biotech universe: every company the SEC classifies as pharma/biotech (SIC 2834, 2836, 8731) that filed
financial statements since 2021, including companies that have since been acquired, delisted or gone bankrupt.
Matched to a price ticker by SEC company ID (CIK), never by ticker alone.

Output: data/universe.csv, reports/universe.md
"""
import csv, io, re, sys, time, zipfile
from pathlib import Path
from common import get

BIOTECH_SIC = {"2834": "Pharmaceutical preparations", "2836": "Biological products", "8731": "Biological research"}
US_EXCH = {"NASDAQ", "NYSE", "NYSE MKT", "NYSE ARCA", "AMEX", "BATS", "NYSE American", "Nasdaq", "NYSEAMERICAN"}
ROOT = Path(__file__).resolve().parent.parent
quarters = [f"CY{y}Q{q}I" for y in range(2021, 2027) for q in range(1, 5)]


def log(*a):
    print(*a, flush=True)


# 1. every company that reported a balance sheet in any quarter since 2021
ciks = {}
for concept in ("CashAndCashEquivalentsAtCarryingValue", "Assets"):
    for qtr in quarters:
        j = get(f"https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/{qtr}.json")
        if not j:
            continue
        for d in j["data"]:
            c = ciks.setdefault(d["cik"], {"name": d["entityName"], "first": d["end"], "last": d["end"]})
            c["first"], c["last"] = min(c["first"], d["end"]), max(c["last"], d["end"])
    log(f"{concept}: {len(ciks):,} companies so far")

# 2. SEC company profile for each -> keep pharma/biotech
bio = []
t0 = time.time()
for i, (cik, c) in enumerate(sorted(ciks.items())):
    if i % 500 == 0:
        log(f"profiles {i:,}/{len(ciks):,} ({time.time()-t0:.0f}s), biotech so far {len(bio)}")
    s = get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    if not s or str(s.get("sic")) not in BIOTECH_SIC:
        continue
    r = s["filings"]["recent"]
    periodic = [(r["filingDate"][k], r["accessionNumber"][k]) for k in range(len(r["form"])) if r["form"][k] in ("10-K", "10-Q")]
    bio.append({"cik": cik, "name": s["name"], "sic": s["sic"], "tickers": "|".join(s.get("tickers") or []),
                "exchanges": "|".join(x or "" for x in (s.get("exchanges") or [])),
                "first_period": c["first"], "last_period": c["last"],
                "last_periodic_accn": max(periodic)[1] if periodic else ""})
log(f"biotech companies: {len(bio):,}")

# 3. companies with no current ticker (acquired/delisted): read the ticker from their last 10-Q/10-K cover page
for b in bio:
    if b["tickers"] or not b["last_periodic_accn"]:
        continue
    acc = b["last_periodic_accn"].replace("-", "")
    idx = get(f"https://www.sec.gov/Archives/edgar/data/{b['cik']}/{acc}/index.json")
    files = [f["name"] for f in (idx or {}).get("directory", {}).get("item", [])]
    inst = next((f for f in files if f.endswith("_htm.xml")), None)
    if inst:
        x = get(f"https://www.sec.gov/Archives/edgar/data/{b['cik']}/{acc}/{inst}", raw=True) or b""
        m = re.findall(rb"<dei:TradingSymbol[^>]*>\s*([A-Za-z.\-]+)\s*<", x)
        if m:
            b["tickers"] = m[0].decode().upper()
            b["exchanges"] = "(delisted)"

# 4. match to Tiingo's ticker list (covers delisted names) by ticker + overlapping dates
z = zipfile.ZipFile(io.BytesIO(get("https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip", raw=True)))
tl = {}
for r in csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]), "utf-8")):
    if r.get("assetType") == "Stock" and r.get("priceCurrency") == "USD" and r.get("startDate"):
        tl.setdefault(r["ticker"].upper(), []).append(r)


def tiingo_match(tick, first, last):
    """Best Tiingo entry whose trading dates overlap the company's reporting period; also tries the
    bankruptcy/OTC suffixes Tiingo uses after a Nasdaq/NYSE delisting (e.g. CLVS -> CLVSQ)."""
    for t in (tick, tick + "Q", tick + "F"):
        for r in tl.get(t, []):
            if r["startDate"] <= last and (r["endDate"] or "9999") >= first[:7]:
                return t, r
    return None, None


rows = []
for b in bio:
    tick = b["tickers"].split("|")[0] if b["tickers"] else ""
    exch = b["exchanges"].split("|")[0] if b["exchanges"] else ""
    active = bool(exch) and exch != "(delisted)"
    tt, tr = tiingo_match(tick, b["first_period"], b["last_period"]) if tick else (None, None)
    listed_exch = active and exch.upper() in {e.upper() for e in US_EXCH}
    was_listed = tr is not None and tr["exchange"] in US_EXCH | {"PINK", "OTC"}
    if not tick:
        status = "no ticker found"
    elif active and listed_exch:
        status = "listed"
    elif active:
        status = f"OTC/other ({exch})"
    else:
        status = "stopped trading" if tr else "stopped trading · no price source"
    rows.append({**{k: b[k] for k in ("cik", "name", "sic", "first_period", "last_period")},
                 "ticker": tick, "exchange": exch, "status": status,
                 "price_source": "twelvedata" if status == "listed" else ("tiingo" if tr else ""),
                 "tiingo_ticker": tt or "", "tiingo_start": tr["startDate"] if tr else "", "tiingo_end": tr["endDate"] if tr else ""})

out = ROOT / "data" / "universe.csv"
out.parent.mkdir(exist_ok=True)
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(sorted(rows, key=lambda r: r["name"]))

from collections import Counter
st = Counter(r["status"] if not r["status"].startswith("OTC") else "OTC/other" for r in rows)
src = Counter(r["price_source"] or "none" for r in rows)
rep = ["# Universe", f"built {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", "",
       f"- Companies reporting financials since 2021 (all sectors): {len(ciks):,}",
       f"- Pharma/biotech by SEC industry code {', '.join(BIOTECH_SIC)}: **{len(rows):,}**", "",
       "| Status | Companies |", "|---|---|"] + [f"| {k} | {v} |" for k, v in st.most_common()] + \
      ["", "| Price source | Companies |", "|---|---|"] + [f"| {k} | {v} |" for k, v in src.most_common()]
(ROOT / "reports" / "universe.md").write_text("\n".join(rep) + "\n")
log("\n".join(rep))
