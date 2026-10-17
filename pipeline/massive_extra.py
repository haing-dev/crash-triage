"""
Two more market data sets from Massive, both bulk (all US stocks per call), kept for the universe's tickers only:

  short interest  FINRA short interest, twice a month (settlement date, shares sold short). A figure counts for an
                  entry day only from 14 days after its settlement date (FINRA publishes it about a week later).
  dividends       cash distributions per share with ex-dividend date (regular and special), added to returns.

Output: data/market/short_interest/<cik>.csv (settlement_date,short_interest)
        data/market/dividends/<cik>.csv (ex_date,cash_amount,type)
State:  data/state/massive_extra.json (how far each set has been read)

    python massive_extra.py --minutes 100
"""
import argparse, csv, json, os, time
from datetime import date
from urllib.parse import urlencode
from common import DATA, get, universe

MK = os.environ.get("MASSIVE_API_KEY", "")
H = {"User-Agent": "crash-triage"}
START = "2020-01-01"
STATE = DATA / "state" / "massive_extra.json"


def pages(url, t_end):
    while url and time.time() < t_end:
        j = get(url + ("&" if "?" in url else "?") + "apiKey=" + MK, headers=H) or {}
        yield j.get("results") or [], j.get("next_url")
        url = j.get("next_url")
        time.sleep(12.5)                                      # free plan: 5 calls a minute


def add(folder, cik, rows, cols):
    p = DATA / "market" / folder / f"{cik}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if p.exists():
        for r in csv.reader(p.open()):
            if r and r[0] != cols[0]:
                old[r[0]] = r
    for r in rows:
        old[r[0]] = r
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(old[k] for k in sorted(old))


def run(kind, base, date_field, cols, row, st, t_end):
    tick = {u["ticker"].upper(): u["cik"] for u in universe().values() if u["ticker"]}
    s = st.setdefault(kind, {})
    url = s.get("next") or f"{base}?{urlencode({date_field + '.gte': s.get('through', START), 'sort': date_field + '.asc', 'limit': s.get('limit', 50000)})}"
    n = 0
    try:
        n = read_pages(kind, url, date_field, cols, row, tick, s, st, t_end)
    except Exception as e:                                    # an expired cursor: restart from the date reached
        print(kind, "stopped:", str(e)[:150])
        s["next"] = None
    STATE.write_text(json.dumps(st, indent=1))
    print(f"{kind}: {n} rows read, through {s.get('through')}")


def read_pages(kind, url, date_field, cols, row, tick, s, st, t_end):
    n = 0
    for res, nxt in pages(url, t_end):
        got = {}
        for r in res:
            cik = tick.get(str(r.get("ticker", "")).upper())
            if cik and r.get(date_field, "9") <= date.today().isoformat():
                got.setdefault(cik, []).append(row(r))
        for cik, rows in got.items():
            add(kind, cik, rows, cols)
        if res:
            valid = [r[date_field] for r in res if r.get(date_field, "9") <= date.today().isoformat()]
            if valid:
                s["through"] = max(s.get("through", START), max(valid))
        s["next"] = nxt
        n += len(res)
        STATE.write_text(json.dumps(st, indent=1))
        if not nxt:
            break
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=100)
    a = ap.parse_args()
    if not MK:
        return print("no MASSIVE_API_KEY")
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    t_end = time.time() + a.minutes * 60
    st.setdefault("dividends", {}).setdefault("limit", 1000)
    run("short_interest", "https://api.massive.com/stocks/v1/short-interest", "settlement_date",
        ["settlement_date", "short_interest"], lambda r: [r["settlement_date"], r["short_interest"]], st, t_end)
    run("dividends", "https://api.massive.com/v3/reference/dividends", "ex_dividend_date",
        ["ex_date", "cash_amount", "type"], lambda r: [r["ex_dividend_date"], r.get("cash_amount"), r.get("dividend_type", "")], st, t_end)


if __name__ == "__main__":
    main()
