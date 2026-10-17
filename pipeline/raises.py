"""
How each company has raised money, read from its own SEC filings with fixed text patterns (no model, no estimates):

  ATM programme   a 424B5 / S-3 prospectus for an at-the-market sales agreement ("at-the-market" + sales /
                  distribution agreement); its size from "aggregate offering price of up to $X"
  priced raise    a 424B4 / 424B5 prospectus or an 8-K reporting an unregistered sale (item 3.02) with a price
                  per share ("public offering price of $X", "at a purchase price of $X per share"), and whether
                  investors also got warrants to buy more shares (pre-funded warrants and underwriter / placement
                  agent warrants are not counted: they are not a sweetener for investors)
The matching sentence is stored with every record, so each one can be checked against the filing.

Output: data/raises/<cik>.json {"done": [accessions read], "raises": [...], "checked": day all filings were read};
incremental, newest filings first.

    python raises.py --minutes 300
"""
import argparse, json, re, time
from datetime import date
from common import DATA, ITEMS, filings, get, sec_url, to_text, universe

SINCE = "2017-06-01"
FORMS = {"424B4", "424B5", "S-3", "S-3/A", "8-K"}
ATM = re.compile(r"at[- ]the[- ]market", re.I)
AGREEMENT = re.compile(r"(?:sales|sale|distribution|equity distribution|open market sale|at[- ]the[- ]market (?:issuance |offering )?sales) agreement", re.I)
ATM_SIZE = re.compile(r"aggregate (?:gross )?(?:offering|sales) (?:price|proceeds) of up to \$\s?([\d,]+(?:\.\d+)?)\s*(million|billion)?", re.I)
PRICE = re.compile(r"(?:(?:combined )?public offering price|offering price|purchase price|price to (?:the )?public)"
                   r"[^$.;]{0,70}?\$\s?(\d{1,4}(?:\.\d{1,6})?)", re.I)
PRICE_8K = re.compile(r"(?:at a|for a|purchase) (?:purchase |offering |combined )?price of \$\s?(\d{1,4}(?:\.\d{1,6})?) per (?:share|unit)", re.I)
WARRANT = re.compile(r"warrants? to purchase", re.I)
NOT_SWEETENER = re.compile(r"pre-?funded|underwriter|placement agent|representative|agent.s|underwriters", re.I)


def snippet(t, m, n=220):
    return t[max(0, m.start() - n // 2):m.end() + n // 2].strip()


def amount(num, unit):
    v = float(num.replace(",", ""))
    return v * (1e6 if (unit or "").lower() == "million" else 1e9 if (unit or "").lower() == "billion" else 1)


def read(form, t):
    head = t[:40000]
    if form != "8-K" and ATM.search(head) and AGREEMENT.search(head):
        m = ATM_SIZE.search(head)
        return {"type": "atm", "atm_usd": amount(*m.groups()) if m else None,
                "quote": snippet(head, m or ATM.search(head))}
    if form.startswith("S-3"):
        return None
    m = (PRICE_8K if form == "8-K" else PRICE).search(head) or (PRICE.search(head) if form == "8-K" else None)
    if not m or not 0 < float(m.group(1)) < 5000:
        return None
    sweet = [w for w in WARRANT.finditer(head) if not NOT_SWEETENER.search(head[max(0, w.start() - 60):w.start()])]
    return {"type": "priced", "price": float(m.group(1)), "warrants": bool(sweet), "quote": snippet(head, m),
            "warrant_quote": snippet(head, sweet[0]) if sweet else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=300)
    a = ap.parse_args()
    t0, out_dir = time.time(), DATA / "raises"
    out_dir.mkdir(exist_ok=True)
    n = 0
    for cik in sorted(universe()):
        p = out_dir / f"{cik}.json"
        rec = json.loads(p.read_text()) if p.exists() else {"done": [], "raises": []}
        done = set(rec["done"])
        try:
            fl = [x for x in filings(cik) if x[0] in FORMS and x[1] >= SINCE and x[2] not in done]
        except Exception as e:
            print(cik, "filings failed", e)
            continue
        fl = [x for x in fl if x[0] != "8-K" or "3.02" in (ITEMS.get(x[2]) or "")]
        for form, fd, acc, doc in sorted(fl, key=lambda x: x[1], reverse=True):
            if time.time() - t0 > a.minutes * 60:
                p.write_text(json.dumps(rec))
                print(f"time budget reached after {n} documents")
                return
            try:
                b = get(sec_url(cik, acc, doc), raw=True)
                r = read(form, to_text(b)) if b else None
            except Exception as e:
                print(cik, acc, "failed", str(e)[:100])
                continue
            n += 1
            rec["done"].append(acc)
            if r:
                rec["raises"].append({"form": form, "filed": fd, "accn": acc, "doc": doc, **r})
        rec["raises"].sort(key=lambda r: r["filed"])
        rec["checked"] = date.today().isoformat()            # every filing up to today has been read
        p.write_text(json.dumps(rec))
        print(f"{cik} {universe()[cik]['ticker']:6s} raises={len(rec['raises'])}", flush=True)
    print(f"all companies done ({n} documents this run)")


if __name__ == "__main__":
    main()
