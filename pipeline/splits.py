"""
Stock splits for companies still trading (Yahoo Finance chart API, free, no key). Stored once per company in
data/splits/<cik>.json and refreshed weekly. Used to turn split-adjusted closes back into the actual price on a
past day (market value). Companies that stopped trading get raw closes from Tiingo instead (prices.py).

    python splits.py --max-age-days 7
"""
import argparse, csv, json, time
from datetime import date, datetime, timezone
from pathlib import Path
from common import get

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "splits"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=10000)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    today = date.today()
    ok = fail = skip = 0
    errors = []
    for u in csv.DictReader((ROOT / "data" / "universe.csv").open()):
        if u["price_source"] != "twelvedata" or not u["ticker"]:
            continue
        p = OUT / f"{u['cik']}.json"
        if p.exists() and (today - date.fromisoformat(json.loads(p.read_text())["checked"])).days < a.max_age_days:
            skip += 1
            continue
        if ok + fail >= a.limit or fail >= 15 and ok == 0:
            break                                         # source refusing us: stop early, keep what we have
        sym = u["ticker"].replace(".", "-")
        try:
            j = get(tries=2, url=f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=1577836800&period2={int(time.time())}"
                    f"&interval=1mo&events=split", headers=UA)
            res = ((j or {}).get("chart") or {}).get("result") or []
            if not res:
                raise RuntimeError("no result")
            ev = (res[0].get("events") or {}).get("splits") or {}
            splits = sorted([datetime.fromtimestamp(v["date"], timezone.utc).date().isoformat(), v["numerator"], v["denominator"]]
                            for v in ev.values())
            p.write_text(json.dumps({"ticker": u["ticker"], "checked": today.isoformat(), "splits": splits}))
            ok += 1
        except Exception as e:
            fail += 1
            errors.append(f"{u['ticker']}: {str(e)[:120]}")
            print(u["ticker"], "failed", str(e)[:100], flush=True)
        time.sleep(0.5)
    msg = f"splits: updated {ok}, still fresh {skip}, failed {fail}"
    (OUT / "_last_run.txt").write_text(msg + "\n" + "\n".join(errors[:20]) + "\n")
    print(msg)


if __name__ == "__main__":
    main()
