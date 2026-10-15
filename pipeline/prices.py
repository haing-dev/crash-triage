"""
Download daily closing prices (split-adjusted) for the universe, within free-plan limits, resumably.
- Still listed  -> Twelve Data (free: 8 calls/min, 800/day)
- Stopped trading -> Tiingo   (free: 50 calls/hour, 1,000/day, 500 tickers/month)
Each run spends at most the budget given on the command line, saves progress, and can be re-run any time.
Prices: data/prices/<cik>.csv (date,close). Progress: data/state/prices.json
Raw (not split-adjusted) closes, fetched once with spare calls, for market value on a past date:
data/prices_raw/<cik>.csv. If a split happens, the daily update notices the old days changed and refetches all.

    python prices.py --twelvedata 380 --tiingo 45
"""
import argparse, csv, json, os, time
from datetime import date, timedelta
from pathlib import Path
from common import get

ROOT = Path(__file__).resolve().parent.parent
PDIR = ROOT / "data" / "prices"
STATE = ROOT / "data" / "state" / "prices.json"
START = "2020-10-01"          # 5 years of crashes + lookback for the first day
TD, TI = os.environ.get("TWELVEDATA_API_KEY", ""), os.environ.get("TIINGO_API_KEY", "")


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"done": {}, "failed": {}}


def save(cik, rows, src, replace=False, folder=None):
    d0 = folder or PDIR
    d0.mkdir(parents=True, exist_ok=True)
    p = d0 / f"{cik}.csv"
    old = {}
    if p.exists() and not replace:
        for r in csv.reader(p.open()):
            if r and r[0] != "date":
                old[r[0]] = r[1]
    for d, c in rows:
        old[d] = c
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "close"])
        for d in sorted(old):
            w.writerow([d, old[d]])
    return len(old)


def twelvedata(sym, start, adjust="splits"):
    j = get(f"https://api.twelvedata.com/time_series?symbol={sym}&interval=1day&start_date={start}"
            f"&outputsize=5000&adjust={adjust}&apikey={TD}")
    if not j or j.get("status") != "ok":
        raise RuntimeError((j or {}).get("message", "no data"))
    return [(v["datetime"][:10], v["close"]) for v in j["values"]]


def tiingo(sym, start, end, field="adjClose"):
    j = get(f"https://api.tiingo.com/tiingo/daily/{sym}/prices?startDate={start}&endDate={end}&token={TI}")
    if not isinstance(j, list):
        raise RuntimeError((j or {}).get("detail", "no data") if isinstance(j, dict) else "no data")
    return [(r["date"][:10], f"{r[field]:.6g}") for r in j]


def changed(cik, rows):
    """True if the overlapping days no longer match what is stored: a split re-based the history."""
    p = PDIR / f"{cik}.csv"
    if not p.exists():
        return False
    old = {r["date"]: float(r["close"]) for r in csv.DictReader(p.open()) if r["close"]}
    for d, c in rows:
        if d in old and old[d] > 0 and abs(float(c) / old[d] - 1) > 0.02:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--twelvedata", type=int, default=380)
    ap.add_argument("--tiingo", type=int, default=45)
    a = ap.parse_args()
    st = load_state()
    uni = list(csv.DictReader((ROOT / "data" / "universe.csv").open()))
    today = date.today().isoformat()
    # daily caps under the free plans (Twelve Data 800/day, Tiingo 1,000/day) tracked across runs
    use = st.setdefault("usage", {}).setdefault(today, {"twelvedata": 0, "tiingo": 0})
    st["usage"] = {today: use}
    budget = {"twelvedata": min(a.twelvedata, 760 - use["twelvedata"]) if TD else 0,
              "tiingo": min(a.tiingo, 950 - use["tiingo"]) if TI else 0}
    got = {"twelvedata": 0, "tiingo": 0}
    # benchmark: biotech index ETF (XBI), refreshed daily
    if TD and st["done"].get("XBI", "") < (date.today() - timedelta(days=1)).isoformat() and budget["twelvedata"] > 0:
        try:
            save("XBI", twelvedata("XBI", START), "twelvedata")
            st["done"]["XBI"] = today
            use["twelvedata"] += 1
            got["twelvedata"] += 1
        except Exception as e:
            print("XBI failed", e)
    for u in uni:
        src, cik = u["price_source"], u["cik"]
        if src not in budget or got[src] >= budget[src]:
            continue
        last = st["done"].get(cik)
        if src == "tiingo" and last:
            continue                                   # stopped trading: history is complete once fetched
        if src == "twelvedata" and last and last >= (date.today() - timedelta(days=1)).isoformat():
            continue                                   # already current
        start = (date.fromisoformat(last) - timedelta(days=7)).isoformat() if last else START
        try:
            replace = False
            if src == "twelvedata":
                rows = twelvedata(u["ticker"], start)
                if last and changed(cik, rows) and got[src] + 1 < budget[src]:
                    rows, replace = twelvedata(u["ticker"], START), True     # split: refetch the whole history
                    got[src] += 1
                    use[src] += 1
                    st.setdefault("raw_done", {}).pop(cik, None)            # and the raw closes
            else:
                rows = tiingo(u["tiingo_ticker"], start, u["tiingo_end"] or today)
            n = save(cik, rows, src, replace)
            st["done"][cik] = today
            st["failed"].pop(cik, None)
            print(f"{src:10s} {u['ticker']:8s} {u['name'][:40]:40s} {n} days", flush=True)
        except Exception as e:
            st["failed"][cik] = f"{today}: {str(e)[:120]}"
            print(f"{src:10s} {u['ticker']:8s} FAILED {e}", flush=True)
        got[src] += 1
        use[src] += 1
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st, indent=0))
    # spare Tiingo calls: raw (not split-adjusted) closes, once per company, crash companies first (latest crash
    # first, so the live screener fills in first). Twelve Data's unadjusted series proved unreliable, so listed
    # companies are fetched from Tiingo too (counts against its 500-symbols-a-month limit: failures retry next month).
    raw = st.setdefault("raw_done", {})
    month = today[:7]
    last_crash = {}
    cp = ROOT / "data" / "crashes.csv"
    for c in (csv.DictReader(cp.open()) if cp.exists() else []):
        last_crash[c["cik"]] = max(last_crash.get(c["cik"], ""), c["date"])
    order = sorted((u for u in uni if u["cik"] in st["done"] and u["price_source"] in ("tiingo", "twelvedata")),
                   key=lambda u: (u["cik"] not in last_crash, "" if u["cik"] not in last_crash else
                                  "".join(chr(255 - ord(ch)) for ch in last_crash[u["cik"]])))
    for u in order:
        cik = u["cik"]
        if not TI or got["tiingo"] >= budget["tiingo"]:
            break
        prev = str(raw.get(cik, ""))
        if prev and not (prev.startswith("failed") and prev[7:14] != month):
            continue                                     # done, or failed this month already
        sym = u["tiingo_ticker"] if u["price_source"] == "tiingo" else u["ticker"].replace(".", "-")
        try:
            rows = tiingo(sym, START, u["tiingo_end"] or today, field="close")
            save(cik, rows, "tiingo", True, ROOT / "data" / "prices_raw")
            raw[cik] = today
        except Exception as e:
            raw[cik] = f"failed {today}: {str(e)[:80]}"
        got["tiingo"] += 1
        use["tiingo"] += 1
        STATE.write_text(json.dumps(st, indent=0))
    print(f"raw closes: {sum(1 for v in raw.values() if not str(v).startswith('failed'))} companies")
    need = {s: sum(1 for u in uni if u["price_source"] == s and u["cik"] not in st["done"]) for s in budget}
    print(f"this run: {got} · still to fetch for the first time: {need} · failures: {len(st['failed'])}")


if __name__ == "__main__":
    main()
