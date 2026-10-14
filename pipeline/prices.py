"""
Download daily closing prices (split-adjusted) for the universe, within free-plan limits, resumably.
- Still listed  -> Twelve Data (free: 8 calls/min, 800/day)
- Stopped trading -> Tiingo   (free: 50 calls/hour, 1,000/day, 500 tickers/month)
Each run spends at most the budget given on the command line, saves progress, and can be re-run any time.
Prices: data/prices/<cik>.csv (date,close). Progress: data/state/prices.json

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


def save(cik, rows, src):
    PDIR.mkdir(parents=True, exist_ok=True)
    p = PDIR / f"{cik}.csv"
    old = {}
    if p.exists():
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


def twelvedata(sym, start):
    j = get(f"https://api.twelvedata.com/time_series?symbol={sym}&interval=1day&start_date={start}"
            f"&outputsize=5000&adjust=splits&apikey={TD}")
    if not j or j.get("status") != "ok":
        raise RuntimeError((j or {}).get("message", "no data"))
    return [(v["datetime"][:10], v["close"]) for v in j["values"]]


def tiingo(sym, start, end):
    j = get(f"https://api.tiingo.com/tiingo/daily/{sym}/prices?startDate={start}&endDate={end}&token={TI}")
    if not isinstance(j, list):
        raise RuntimeError((j or {}).get("detail", "no data") if isinstance(j, dict) else "no data")
    return [(r["date"][:10], f"{r['adjClose']:.6g}") for r in j]


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
            if src == "twelvedata":
                rows = twelvedata(u["ticker"], start)
            else:
                rows = tiingo(u["tiingo_ticker"], start, u["tiingo_end"] or today)
            n = save(cik, rows, src)
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
    need = {s: sum(1 for u in uni if u["price_source"] == s and u["cik"] not in st["done"]) for s in budget}
    print(f"this run: {got} · still to fetch for the first time: {need} · failures: {len(st['failed'])}")


if __name__ == "__main__":
    main()
