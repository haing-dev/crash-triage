"""
Actual (not split-adjusted) daily closes from Tiingo for companies whose actual price can't be rebuilt from a stored
split list (companies that stopped trading). Needed for market value on a past day. Paced to the free plan (50 calls
an hour, 500 symbols a month); companies with episodes first.

Output: data/prices_raw/<cik>.csv (a file there = done); failures in data/state/raw_closes.json.

    python raw_closes.py --minutes 300
"""
import argparse, csv, json, time
from datetime import date
from common import DATA, universe
from prices import START, TI, save, tiingo

STATE = DATA / "state" / "raw_closes.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=300)
    a = ap.parse_args()
    if not TI:
        return print("no TIINGO_API_KEY")
    t_end = time.time() + a.minutes * 60
    failed = json.loads(STATE.read_text()) if STATE.exists() else {}
    month, today = date.today().isoformat()[:7], date.today().isoformat()
    with_eps = set()
    for k in ("crash", "slide"):
        p = DATA / k / "episodes.csv"
        if p.exists():
            with_eps |= {r["cik"] for r in csv.DictReader(p.open())}
    todo = [u for u in universe().values() if u["price_source"] == "tiingo" and u["tiingo_ticker"]
            and not (DATA / "splits" / f"{u['cik']}.json").exists() and not (DATA / "prices_raw" / f"{u['cik']}.csv").exists()
            and failed.get(u["cik"], "")[:7] != month]
    todo.sort(key=lambda u: u["cik"] not in with_eps)
    n = 0
    for u in todo:
        if time.time() > t_end:
            break
        try:
            rows = tiingo(u["tiingo_ticker"], START, u["tiingo_end"] or today, field="close")
            save(u["cik"], rows, "tiingo", True, DATA / "prices_raw")
            n += 1
        except Exception as e:
            failed[u["cik"]] = f"{today}: {str(e)[:80]}"
            STATE.write_text(json.dumps(failed, indent=0))
            if "429" in str(e) or "limit" in str(e).lower():
                print("rate limited:", str(e)[:120])
                break
        time.sleep(75)                                    # 48 calls an hour
    print(f"raw closes fetched: {n}; still missing: {len(todo) - n}")


if __name__ == "__main__":
    main()
