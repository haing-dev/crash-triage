"""Smoke test: can each data source be reached from GitHub Actions? Writes reports/smoke.md (no secrets)."""
import io, json, os, subprocess, time, urllib.request, zipfile, csv

UA = os.environ.get("SEC_USER_AGENT", "")
out = ["# Smoke test", f"run: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", ""]


def get(url, headers=None, raw=False):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "crash-triage-smoke"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if raw else json.loads(data)


def check(name, fn):
    try:
        out.append(f"- **{name}**: OK — {fn()}")
    except Exception as e:
        msg = str(e)
        for k in ("TIINGO_API_KEY", "TWELVEDATA_API_KEY"):
            if os.environ.get(k):
                msg = msg.replace(os.environ[k], "***")
        out.append(f"- **{name}**: FAIL — {msg[:300]}")


TD = os.environ.get("TWELVEDATA_API_KEY", "")
TI = os.environ.get("TIINGO_API_KEY", "")


def td(sym, start=None, end=None):
    q = f"symbol={sym}&interval=1day&outputsize=5000&apikey={TD}"
    if start:
        q += f"&start_date={start}&end_date={end}"
    j = get(f"https://api.twelvedata.com/time_series?{q}")
    if j.get("status") != "ok":
        raise RuntimeError(j.get("message", j))
    v = j["values"]
    return f"{len(v)} daily closes, {v[-1]['datetime']} → {v[0]['datetime']}"


def ti(sym, start, end):
    j = get(f"https://api.tiingo.com/tiingo/daily/{sym}/prices?startDate={start}&endDate={end}&token={TI}")
    if isinstance(j, dict):
        raise RuntimeError(j.get("detail", j))
    return f"{len(j)} daily closes, {j[0]['date'][:10]} → {j[-1]['date'][:10]}" if j else "0 rows"


def ti_list():
    z = zipfile.ZipFile(io.BytesIO(get("https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip", raw=True)))
    rows = list(csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]), "utf-8")))
    us = [r for r in rows if r.get("exchange") in ("NASDAQ", "NYSE", "NYSE MKT", "AMEX") and r.get("assetType") == "Stock"]
    clvs = [r for r in rows if r["ticker"].upper() == "CLVS"]
    return f"{len(rows):,} tickers listed ({len(us):,} US stocks); CLVS entry: {clvs[0] if clvs else 'not found'}"


def sec():
    if not UA:
        raise RuntimeError("SEC_USER_AGENT secret missing")
    h = {"User-Agent": UA}
    s = get("https://data.sec.gov/submissions/CIK0000875320.json", h)
    f = get("https://data.sec.gov/api/xbrl/companyconcept/CIK0000875320/us-gaap/CashAndCashEquivalentsAtCarryingValue.json", h)
    n = len(f["units"]["USD"])
    return f"{s['name']} · SIC {s['sic']} ({s['sicDescription']}) · {n} cash data points via XBRL"


def ctgov():
    j = get("https://clinicaltrials.gov/api/v2/studies?query.spons=Vertex&pageSize=1&countTotal=true&fields=NCTId,BriefTitle,Phase")
    st = j["studies"][0]["protocolSection"]
    return f"{j.get('totalCount')} Vertex-sponsored studies; e.g. {st['identificationModule']['nctId']}"


def claude():
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        raise RuntimeError("CLAUDE_CODE_OAUTH_TOKEN secret missing")
    r = subprocess.run(["claude", "-p", "Reply with exactly: ok", "--model", "sonnet", "--effort", "low",
                        "--tools", "", "--output-format", "text"], capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[:300])
    return f"Claude Code replied {r.stdout.strip()!r}"


check("Twelve Data · listed stock (VRTX)", lambda: td("VRTX"))
check("Twelve Data · delisted stock (CLVS, 2022)", lambda: td("CLVS", "2022-01-01", "2022-12-31"))
check("Tiingo · listed stock (VRTX)", lambda: ti("VRTX", "2026-09-01", "2026-10-13"))
check("Tiingo · delisted stock (CLVS, 2022)", lambda: ti("CLVS", "2022-01-01", "2022-12-31"))
check("Tiingo · full ticker list incl. delisted", ti_list)
check("SEC EDGAR · filings + XBRL (Vertex)", sec)
check("ClinicalTrials.gov API v2", ctgov)
check("Claude Code · subscription token", claude)
os.makedirs("reports", exist_ok=True)
open("reports/smoke.md", "w").write("\n".join(out) + "\n")
print("\n".join(out))
