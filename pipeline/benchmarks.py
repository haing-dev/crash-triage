"""
One-off backfill for the benchmarks: the S&P 500 ETF (SPY) price history from Twelve Data (split-adjusted, like XBI),
and the full cash-distribution history of SPY and XBI from Massive, so total returns include their dividends. Daily
updates afterwards come from prices.py (closes) and massive_extra.py (new dividends).

Output: data/prices/SPY.csv, data/market/dividends/{SPY,XBI}.csv
"""
import csv, os
from common import DATA, get
from prices import START, save, twelvedata

MK = os.environ.get("MASSIVE_API_KEY", "")
rows = twelvedata("SPY", START)
print("SPY closes:", save("SPY", rows, "twelvedata"))
for t in ("SPY", "XBI"):
    j = get(f"https://api.massive.com/v3/reference/dividends?ticker={t}&ex_dividend_date.gte={START}&limit=1000&sort=ex_dividend_date&order=asc&apiKey={MK}",
            headers={"User-Agent": "crash-triage"}) or {}
    res = j.get("results") or []
    p = DATA / "market" / "dividends" / f"{t}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ex_date", "cash_amount", "type"])
        for r in res:
            w.writerow([r["ex_dividend_date"], r.get("cash_amount"), r.get("dividend_type", "")])
    print(t, "dividends:", len(res))
