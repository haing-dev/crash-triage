"""One-off checks of new data sources (output committed to reports/probe.md)."""
import json, os, re
from common import get, filings, sec_url
MK = os.environ.get("MASSIVE_API_KEY", "")
H = {"User-Agent": "crash-triage"}
out = []
def tryget(label, url, headers=None):
    try:
        j = get(url, headers=headers, tries=2)
        s = json.dumps(j)[:700] if j is not None else "None (404)"
    except Exception as e:
        s = "ERROR " + str(e)[:300]
    out.append(f"## {label}\n```\n{s}\n```")
tryget("short interest (ticker)", f"https://api.massive.com/stocks/v1/short-interest?ticker=SRPT&limit=3&sort=settlement_date.asc&apiKey={MK}", H)
tryget("short interest (date)", f"https://api.massive.com/stocks/v1/short-interest?settlement_date=2021-06-15&limit=3&apiKey={MK}", H)
tryget("dividends v1", f"https://api.massive.com/stocks/v1/dividends?ticker=SAGE&limit=3&apiKey={MK}", H)
tryget("dividends v3", f"https://api.massive.com/v3/reference/dividends?ex_dividend_date.gte=2021-01-01&limit=3&apiKey={MK}", H)
tryget("float", f"https://api.massive.com/stocks/v1/float?ticker=SRPT&limit=3&apiKey={MK}", H)
# SEC: are 13G filings about a company listed in its own submissions?
rows = filings("873303")   # Sarepta
forms = {}
for f, d, a, doc in rows:
    forms[f] = forms.get(f, 0) + 1
out.append("## Sarepta forms\n" + json.dumps(forms))
g = [(f, d, a, doc) for f, d, a, doc in rows if "13G" in f or "13D" in f][:3]
out.append("## 13G samples\n" + json.dumps(g))
if g:
    b = get(sec_url("873303", g[0][2], g[0][3]), raw=True) or b""
    m = re.search(rb"CUSIP[^0-9A-Z]{0,40}([0-9A-Z]{6}[ -]?[0-9A-Z]{2}[ -]?[0-9])", b)
    out.append("## CUSIP match: " + (m.group(1).decode() if m else "none") + "\n```\n" + b[:600].decode("utf8", "replace") + "\n```")
p = get("https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets", raw=True) or b""
links = sorted(set(re.findall(rb'href="([^"]+13f[^"]*\.zip)"', p, re.I)))
out.append("## 13F zips\n" + "\n".join(l.decode() for l in links))
open("../reports/probe.md", "w").write("\n\n".join(out))
