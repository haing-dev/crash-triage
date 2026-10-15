"""
The cash items the XBRL numbers don't carry, read by Claude from the filings. Each item needs a verbatim quote that is
checked against the text; unverified items are dropped.

  one-off receipts   non-recurring cash received inside the six-month burn window (collaboration upfront, milestone,
                     asset or voucher sale) -> added back to cash used, so they aren't assumed to repeat
  one-off payments   clearly non-recurring cash paid in that window (completed severance, one-time licence fee)
                     -> taken out of cash used
  financings         cash actually received after the balance-sheet date and by the entry day
  warrants           outstanding warrants (count, exercise price, expiry); fit.py counts those in the money at entry

Sources, all public by the entry day: the latest 10-Q/10-K (liquidity section, subsequent events, warrants note) and
the 8-Ks and prospectuses filed between the balance-sheet date and the entry day.

Output: data/<kind>/liquidity/<cik>_<date>.json (once per episode; newest first)

    python liquidity.py --kind crash --limit 40 --shard 0/2
"""
import argparse, json, re
from common import ask_claude, filings, get, kdir, norm, quoted, sec_text, sec_url, to_text, KINDS

SCHEMA = """Return ONLY a JSON object, no prose:
{
 "one_off_receipts": [{"amount_usd": number, "what": "short description", "quote": "verbatim"}],
 "one_off_payments": [{"amount_usd": number, "what": "short description", "quote": "verbatim"}],
 "financings_after_balance_sheet": [{"date": "YYYY-MM-DD", "net_proceeds_usd": number, "what": "short", "quote": "verbatim"}],
 "warrants": [{"count": number, "exercise_price_usd": number, "expiry": "YYYY-MM-DD or null", "quote": "verbatim"}]
}
Definitions:
- one_off_receipts: non-recurring cash RECEIVED within the burn window that sits inside operating cash flow:
  collaboration/licence upfront payments, milestone payments, sale of a priority review voucher or asset. Not recurring
  product sales or routine reimbursements. Only amounts received inside the burn window.
- one_off_payments: clearly non-recurring operating cash PAID within the burn window (completed severance/restructuring,
  one-time licence or settlement payment). Not routine R&D or salaries.
- financings_after_balance_sheet: cash actually received AFTER the balance-sheet date and ON OR BEFORE the entry date
  (public offering, private placement, registered direct, ATM sales, loan drawdown, warrant exercises). Net proceeds if
  given, else gross. Exclude financings only announced but not closed by the entry date.
- warrants: warrants to buy common stock outstanding at the latest date given. One entry per class/price.
Use [] when the documents don't say. Amounts in US dollars (convert "$12.5 million" -> 12500000).
Every quote must be copied EXACTLY from the documents (<= 40 words). Do not use outside knowledge."""
FIELDS = ("one_off_receipts", "one_off_payments", "financings_after_balance_sheet", "warrants")


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


def gather(rec):
    cik, parts = rec["cik"], []
    accn = (rec.get("balance") or {}).get("accn")
    t = sec_text(cik, accn) if accn else None
    if t:
        lq = windows(t, r"Liquidity and Capital Resources", 0, 14000, 3)
        parts.append("=== LATEST 10-Q/10-K: LIQUIDITY ===\n" + (lq[-1] if lq else ""))
        parts.append("=== SUBSEQUENT EVENTS ===\n" + "\n...\n".join(windows(t, r"Subsequent Events?", 0, 2500, 2)))
        parts.append("=== WARRANTS ===\n" + "\n...\n".join(windows(t, r"warrants? (?:to purchase|outstanding|exercisable)|exercise price", 400, 500, 5)))
    end = (rec.get("balance") or {}).get("end")
    if end:
        fin = []
        for form, fd, acc, doc in filings(cik):
            if form in ("8-K", "424B5", "424B4", "424B3", "6-K") and end < fd <= rec["asof"] and len(fin) < 6:
                b = get(sec_url(cik, acc, doc), raw=True)
                w = windows(to_text(b), r"gross proceeds|net proceeds|private placement|registered direct|drew down|drawdown", 500, 500, 2) if b else []
                if w:
                    fin.append(f"[{form} filed {fd}]\n" + "\n...\n".join(w))
        parts.append("=== FILINGS BETWEEN BALANCE SHEET AND ENTRY ===\n" + "\n\n".join(fin))
    return "\n\n".join(parts)[:45000]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=KINDS, required=True)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--shard", default="0/1", help="i/n: this runner takes episodes whose position %% n == i")
    a = ap.parse_args()
    si, sn = map(int, a.shard.split("/"))
    src, out_dir = kdir(a.kind, "inputs"), kdir(a.kind, "liquidity")
    n = done = 0
    for pos, p in enumerate(sorted(src.glob("*.json"), key=lambda p: p.stem.split("_")[1], reverse=True)):
        if pos % sn != si:
            continue
        out = out_dir / p.name
        if out.exists() and not json.loads(out.read_text()).get("error"):
            done += 1
            continue
        rec = json.loads(p.read_text())
        if not rec.get("balance"):
            continue                                         # nothing to adjust without a balance sheet
        if n >= a.limit:
            break
        n += 1
        b6 = rec.get("burn6")
        res = {"id": rec["id"], "asof": rec["asof"]}
        try:
            text = gather(rec)
            prompt = (f"Company: {rec['name']}. Entry date: {rec['asof']}. Balance-sheet date: {rec['balance']['end']}. "
                      f"Burn window: {b6['start'] + ' to ' + b6['end'] if b6 else 'the latest reported six months'}.\n"
                      f"Read the filing excerpts on standard input.\n\n{SCHEMA}")
            j, nt = ask_claude(prompt, text), norm(text)
            res["chars"] = len(text)
            for k in FIELDS:
                got = [x for x in (j.get(k) or []) if isinstance(x, dict)]
                res[k] = [x for x in got if quoted(x.get("quote"), nt)]
                res[k + "_dropped"] = len(got) - len(res[k])
        except Exception as e:
            res["error"] = str(e)[:300]
        out.write_text(json.dumps(res, indent=1))
        print(f"{a.kind} {rec['date']} {rec['name'][:28]:28s} fin={len(res.get('financings_after_balance_sheet', []))} "
              f"war={len(res.get('warrants', []))} {res.get('error', '')[:80]}", flush=True)
    print(f"{a.kind}: read this run {n} · already done {done}")


if __name__ == "__main__":
    main()
