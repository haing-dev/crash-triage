"""
The facts cash runway needs that the XBRL numbers don't carry, read by Claude from the filings with a verbatim quote
for each (quotes are checked against the text; unverified items are dropped):

  one-off receipts   non-recurring cash received inside the six-month burn window (collaboration upfront, milestone,
                     asset or voucher sale) -> added back to cash used, so they aren't assumed to repeat
  one-off payments   clearly non-recurring cash paid in that window (completed severance, one-time licence fee)
                     -> taken out of cash used
  financings         cash actually received after the balance-sheet date and by the trough (offerings, PIPEs,
                     loan draws, ATM sales)
  warrants           outstanding warrants: count, exercise price, expiry (fit.py counts only those in the money at
                     the trough)
  next catalyst      the next important clinical or regulatory result expected after the crash, with its timing

Sources, all public by the trough: the latest 10-Q/10-K (liquidity, subsequent events, warrants, milestones), the
8-Ks and prospectuses filed between the balance-sheet date and the trough, and the crash announcement.

Output: data/liquidity/<cik>_<date>.json (once per crash)

    python liquidity.py --limit 60
"""
import argparse, json, re, shutil, subprocess, tempfile
from datetime import date, timedelta
from pathlib import Path
from common import get
import factors_raw as FR
from events import norm, to_text

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "liquidity"
RAW = ROOT / "data" / "factors_raw"
SCHEMA = """Return ONLY a JSON object, no prose:
{
 "one_off_receipts": [{"amount_usd": number, "what": "short description", "quote": "verbatim"}],
 "one_off_payments": [{"amount_usd": number, "what": "short description", "quote": "verbatim"}],
 "financings_after_balance_sheet": [{"date": "YYYY-MM-DD", "net_proceeds_usd": number, "what": "short", "quote": "verbatim"}],
 "warrants": [{"count": number, "exercise_price_usd": number, "expiry": "YYYY-MM-DD or null", "quote": "verbatim"}],
 "next_catalyst": {"what": "e.g. Phase 3 topline data for drug X in disease Y", "timing_text": "e.g. second half of 2027",
                   "latest_date": "YYYY-MM-DD (the last day of the stated window)", "quote": "verbatim"} or null
}
Definitions:
- one_off_receipts: non-recurring cash RECEIVED within the burn window that sits inside operating cash flow:
  collaboration/licence upfront payments, milestone payments, sale of a priority review voucher or asset. Not recurring
  product sales or routine reimbursements. Only amounts received inside the burn window.
- one_off_payments: clearly non-recurring operating cash PAID within the burn window (completed severance/restructuring,
  one-time licence or settlement payment). Not routine R&D or salaries.
- financings_after_balance_sheet: cash actually received AFTER the balance-sheet date and ON OR BEFORE the trough date
  (public offering, private placement, registered direct, ATM sales, loan drawdown, warrant exercises). Use net proceeds
  if given, else gross. Exclude financings only announced but not closed by the trough date.
- warrants: warrants to buy common stock outstanding at the latest date given. One entry per class/price.
- next_catalyst: the next important clinical or regulatory result the company expects AFTER the crash date (trial
  readout, topline data, regulatory decision/PDUFA, approval filing acceptance). Pick the soonest material one.
  latest_date = end of the stated window (e.g. "mid-2027" -> 2027-06-30, "second half of 2027" -> 2027-12-31,
  "Q1 2027" -> 2027-03-31, "2027" -> 2027-12-31). null if none is stated.
Use [] or null when the documents don't say. Amounts in US dollars (convert "$12.5 million" -> 12500000).
Every quote must be copied EXACTLY from the documents (<= 40 words). Do not use outside knowledge."""


def windows(t, pat, before, after, n):
    out, last = [], -1
    for m in re.finditer(pat, t, re.I):
        if m.start() < last:
            continue
        a, b = max(0, m.start() - before), min(len(t), m.end() + after)
        out.append(t[a:b])
        last = b
        if len(out) >= n:
            break
    return out


def gather(rec, release_url):
    cik = rec["cik"]
    parts = []
    accn = (rec.get("balance") or {}).get("accn") or (rec.get("burn") or {}).get("accn")
    t = FR.doc_text(cik, accn) if accn else None
    if t:
        lq = windows(t, r"Liquidity and Capital Resources", 0, 14000, 1)
        lq = lq[-1:] if lq else []
        parts.append("=== LATEST 10-Q/10-K: LIQUIDITY ===\n" + "\n".join(lq))
        parts.append("=== SUBSEQUENT EVENTS ===\n" + "\n...\n".join(windows(t, r"Subsequent Events?", 0, 2500, 2)))
        parts.append("=== WARRANTS ===\n" + "\n...\n".join(windows(t, r"warrants? (?:to purchase|outstanding|exercisable)|exercise price", 400, 500, 5)))
        parts.append("=== EXPECTED MILESTONES ===\n" + "\n...\n".join(
            windows(t, r"(?:expect|anticipate|plan)[^.]{0,120}(?:topline|data|readout|results|PDUFA|BLA|NDA|approval)", 200, 300, 8)))
    # 8-Ks and prospectuses between the balance-sheet date and the trough: financings
    end = (rec.get("balance") or {}).get("end")
    if end:
        fin = []
        for form, fd, acc, doc in FR.subs(cik):
            if form in ("8-K", "424B5", "424B4", "424B3", "6-K") and end < fd <= rec["asof"] and len(fin) < 6:
                b = get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc.split('/')[-1]}", raw=True)
                if b:
                    w = windows(to_text(b), r"gross proceeds|net proceeds|private placement|registered direct|drew down|draw(?:down)?", 500, 500, 2)
                    if w:
                        fin.append(f"[{form} filed {fd}]\n" + "\n...\n".join(w))
        parts.append("=== FILINGS BETWEEN BALANCE SHEET AND TROUGH ===\n" + "\n\n".join(fin))
    if release_url:
        b = get(release_url, raw=True)
        if b:
            parts.append("=== CRASH ANNOUNCEMENT ===\n" + to_text(b)[:9000])
    return "\n\n".join(parts)[:45000]


def ask(text, rec, b6):
    exe = shutil.which("claude")
    win = f"{b6['start']} to {b6['end']}" if b6 else "the latest reported six months"
    prompt = (f"Company: {rec['name']}. Crash (news) date: {rec['date']}. Trough date: {rec['asof']}. "
              f"Balance-sheet date: {(rec.get('balance') or {}).get('end')}. Burn window: {win}.\n"
              f"Read the filing excerpts on standard input.\n\n{SCHEMA}")
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([exe, "-p", prompt, "--model", "sonnet", "--effort", "medium", "--tools", "",
                            "--strict-mcp-config", "--no-session-persistence", "--output-format", "text"],
                           input=text, capture_output=True, text=True, timeout=600, cwd=td)
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if r.returncode != 0 or not m:
        raise RuntimeError((r.stderr or r.stdout)[:300])
    return json.loads(m.group(0))


def ok(q, nt):
    return bool(q) and all(norm(f) in nt for f in re.split(r"…|\.\.\.", q) if len(norm(f).split()) >= 3)


def verify(j, text):
    nt = norm(text)
    out = {}
    for k in ("one_off_receipts", "one_off_payments", "financings_after_balance_sheet", "warrants"):
        out[k] = [x for x in (j.get(k) or []) if isinstance(x, dict) and ok(x.get("quote"), nt)]
        out[k + "_dropped"] = len(j.get(k) or []) - len(out[k])
    c = j.get("next_catalyst")
    out["next_catalyst"] = c if isinstance(c, dict) and ok(c.get("quote"), nt) and c.get("latest_date") else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    n = done = 0
    for p in sorted(RAW.glob("*.json"), key=lambda p: p.stem.split("_")[1], reverse=True):   # newest crashes first
        rec = json.loads(p.read_text())
        out = OUT / p.name
        if out.exists() and not json.loads(out.read_text()).get("error"):
            done += 1
            continue
        if n >= a.limit:
            break
        n += 1
        ev = ROOT / "data" / "events" / p.name
        url = ((json.loads(ev.read_text()).get("source") or {}).get("url")) if ev.exists() else None
        res = {"id": rec["id"], "asof": rec["asof"]}
        try:
            text = gather(rec, url)
            res["chars"] = len(text)
            res.update(verify(ask(text, rec, rec.get("burn6")), text))
        except Exception as e:
            res["error"] = str(e)[:300]
        out.write_text(json.dumps(res, indent=1))
        c = res.get("next_catalyst") or {}
        print(f"{rec['date']} {rec['name'][:28]:28s} fin={len(res.get('financings_after_balance_sheet', []))} "
              f"rcpt={len(res.get('one_off_receipts', []))} war={len(res.get('warrants', []))} next={c.get('latest_date')} "
              f"{res.get('error', '')[:80]}", flush=True)
    print(f"read this run: {n} · already done: {done}")


if __name__ == "__main__":
    main()
