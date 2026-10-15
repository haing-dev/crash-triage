"""
Stock splits for companies still trading, from Massive (formerly Polygon.io; free plan). One bulk listing of every
US split since 2020-10 takes a few calls, so this runs weekly and stores the result:
data/splits/<cik>.json = {"splits": [[execution_date, split_to, split_from], ...]} for every still-listed company
(empty list = no split). Used to turn split-adjusted closes back into the actual price on a past day.

    MASSIVE_API_KEY=... python splits.py
"""
import csv, json, os, time
from datetime import date
from pathlib import Path
from common import get

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "splits"
KEY = os.environ.get("MASSIVE_API_KEY", "")


def main():
    if not KEY:
        print("MASSIVE_API_KEY not set: skipping")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    url = "https://api.massive.com/stocks/v1/splits?execution_date.gte=2020-10-01&limit=5000&sort=execution_date.asc"
    rows, pages = [], 0
    while url:
        j = get(url + ("&" if "?" in url else "?") + f"apiKey={KEY}", headers={"User-Agent": "crash-triage"})
        if not j or j.get("status") not in ("OK", "DELAYED"):
            raise SystemExit(f"splits listing failed: {str(j)[:200]}")
        rows += j.get("results", [])
        url, pages = j.get("next_url"), pages + 1
        if url:
            time.sleep(13)                                  # free plan: 5 calls a minute
    by_tick = {}
    for r in rows:
        if r.get("adjustment_type") in ("forward_split", "reverse_split") and r.get("split_from") and r.get("split_to"):
            by_tick.setdefault(r["ticker"].upper(), []).append([r["execution_date"], r["split_to"], r["split_from"]])
    n = 0
    for u in csv.DictReader((ROOT / "data" / "universe.csv").open()):
        if u["price_source"] != "twelvedata" or not u["ticker"]:
            continue
        sp = sorted(by_tick.get(u["ticker"].upper().replace(".", "-"), []) + by_tick.get(u["ticker"].upper(), []))
        sp = [s for i, s in enumerate(sp) if s not in sp[:i]]
        (OUT / f"{u['cik']}.json").write_text(json.dumps({"ticker": u["ticker"], "checked": date.today().isoformat(), "splits": sp}))
        n += bool(sp)
    (OUT / "_last_run.txt").write_text(f"{date.today()}: {len(rows)} splits listed in {pages} calls; {n} listed companies had one\n")
    print(f"{len(rows)} splits in {pages} calls; {n} listed companies in the universe had a split")


if __name__ == "__main__":
    main()
