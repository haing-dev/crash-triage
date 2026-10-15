"""
Shared helpers for every step.

Layout: one "kind" per triage, same files for each:
  data/<kind>/episodes.csv   crash or slide episodes (episodes.py)
  data/<kind>/inputs/        factor inputs as of the entry day (inputs.py)
  data/<kind>/liquidity/     one-offs, later financings and warrants read from filings (liquidity.py)
  data/<kind>/events/        the crash announcement (events.py, crashes only)
  data/<kind>/scores/        factor cells, score and verdict (fit.py, rebuilt every site build, not committed)
Shared: data/universe.csv, data/prices/, data/prices_raw/, data/splits/, data/state/
"""
import csv, functools, html, json, os, re, shutil, subprocess, tempfile, time, urllib.error, urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KINDS = ("crash", "slide")
SEC_UA = os.environ.get("SEC_USER_AGENT", "crash-triage")
_last = {}
_MIN_GAP = {"sec": 0.13, "tiingo": 0.0, "twelvedata": 7.6, "ctgov": 0.2}   # seconds between calls per host


def kdir(kind, sub=""):
    p = DATA / kind / sub if sub else DATA / kind
    p.mkdir(parents=True, exist_ok=True)
    return p


def episodes(kind):
    p = DATA / kind / "episodes.csv"
    return list(csv.DictReader(p.open())) if p.exists() else []


@functools.lru_cache(maxsize=None)
def universe():
    return {u["cik"]: u for u in csv.DictReader((DATA / "universe.csv").open())}


@functools.lru_cache(maxsize=64)
def closes(key):
    """Daily closes (split-adjusted) as two parallel lists: dates, prices."""
    p = DATA / "prices" / f"{key}.csv"
    rows = [(r["date"], float(r["close"])) for r in csv.DictReader(p.open()) if r["close"]] if p.exists() else []
    return [d for d, _ in rows], [c for _, c in rows]


def money(v):
    a = abs(v)
    return (f"${a / 1e9:.1f}B" if a >= 1e9 else f"${a / 1e6:.0f}M" if a >= 1e7 else f"${a / 1e6:.1f}M" if a >= 1e6
            else f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:.0f}")


# ---------------------------------------------------------------- HTTP

def _host_key(url):
    for k, s in (("sec", "sec.gov"), ("tiingo", "tiingo"), ("twelvedata", "twelvedata"), ("ctgov", "clinicaltrials")):
        if s in url:
            return k
    return "other"


def get(url, raw=False, tries=5, headers=None):
    key = _host_key(url)
    h = headers or ({"User-Agent": SEC_UA, "Accept-Encoding": "identity"} if key == "sec" else {"User-Agent": "crash-triage"})
    for i in range(tries):
        gap = _MIN_GAP.get(key, 0) - (time.time() - _last.get(key, 0))
        if gap > 0:
            time.sleep(gap)
        _last[key] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                data = r.read()
            return data if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(5 * (i + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < tries - 1:
                time.sleep(5 * (i + 1))
                continue
            raise


# ---------------------------------------------------------------- SEC filings (cached per company / per document)

@functools.lru_cache(maxsize=4)
def filings(cik):
    """Every filing of a company: list of (form, filing date, accession, primary document)."""
    s = get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json") or {}
    pages = [s.get("filings", {}).get("recent", {})]
    pages += [get(f"https://data.sec.gov/submissions/{fl['name']}") or {} for fl in s.get("filings", {}).get("files", [])]
    rows = []
    for r in pages:
        rows += list(zip(r.get("form", []), r.get("filingDate", []), r.get("accessionNumber", []), r.get("primaryDocument", [])))
    return rows


def sec_url(cik, accn, doc):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{doc.split('/')[-1]}"


@functools.lru_cache(maxsize=8)
def sec_text(cik, accn, doc=None):
    """Plain text of a filing document (the primary one unless `doc` is given)."""
    doc = doc or next((d for fm, fd, a, d in filings(cik) if a == accn), None)
    b = get(sec_url(cik, accn, doc), raw=True) if doc else None
    return to_text(b) if b else None


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, t, a):
        self.skip += t in ("script", "style")
        if t in ("p", "div", "br", "tr", "li", "h1", "h2", "h3"):
            self.out.append("\n")

    def handle_endtag(self, t):
        self.skip -= t in ("script", "style")

    def handle_data(self, d):
        if not self.skip:
            self.out.append(d)


def to_text(b):
    p = _Text()
    p.feed(b.decode("utf-8", "replace"))
    t = html.unescape("".join(p.out))
    return re.sub(r"[ \t\xa0]+", " ", re.sub(r"\n\s*\n+", "\n\n", t)).strip()


# ---------------------------------------------------------------- Claude, with quote checking

def ask_claude(prompt, text):
    """Claude Code headless (subscription token), Sonnet at medium effort, no tools. Returns the JSON it printed."""
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([shutil.which("claude"), "-p", prompt, "--model", "sonnet", "--effort", "medium", "--tools", "",
                            "--strict-mcp-config", "--no-session-persistence", "--output-format", "text"],
                           input=text, capture_output=True, text=True, timeout=600, cwd=td)
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if r.returncode != 0 or not m:
        raise RuntimeError((r.stderr or r.stdout)[:300])
    return json.loads(m.group(0))


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%$.]+", " ", s.lower().replace("’", "'"))).strip()


def quoted(q, normalised_text):
    """True if the quote (pieces split on ellipses) appears word for word in the source text."""
    return bool(q) and all(norm(f) in normalised_text for f in re.split(r"…|\.\.\.", q) if len(norm(f).split()) >= 3)
