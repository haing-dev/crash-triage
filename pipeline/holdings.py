"""
What specialist biotech funds held, from the SEC's Form 13F data sets (every 13F-HR filing's holdings, quarterly).

  company CUSIPs  each company's 6-character CUSIP issuer code, read from the Schedule 13G / 13D filings about it
                  (XML field issuerCusip, or "CUSIP No." on older cover pages)
  specialist      a 13F filer with at least half of the reported value of a filing in companies of this biotech
                  universe, spread over at least 5 of them (decided filing by filing, so the rule needs no list of names)
  position        shares of the company's common stock (SH, not options) in that filing

Output: data/holdings/cusips.json {cik: [issuer codes]}
        data/holdings/filings.csv  accession,filer_cik,filer,period,filed,universe_share,positions
        data/holdings/positions.csv accession,cik,shares
State:  data/state/holdings.json (data sets already read)

    python holdings.py --minutes 300
"""
import argparse, csv, io, json, re, time, zipfile
from common import DATA, filings, get, sec_url, universe

D = DATA / "holdings"
STATE = DATA / "state" / "holdings.json"
FIRST = "2020q3"
CUSIP_XML = re.compile(rb"issuerCusip>\s*([0-9A-Za-z]{6})", re.I)
CUSIP_TXT = re.compile(rb"CUSIP\s*(?:No\.?|Number|#)?\s*[:.]?\s*(?:<[^>]*>\s*)*([0-9A-Z]{6})[ -]?[0-9A-Z]{2}[ -]?[0-9]", re.I)
CUSIP_BEFORE = re.compile(rb"([0-9A-Z]{6})[ -]?[0-9A-Z]{2}[ -]?[0-9]\s*(?:<[^>]*>\s*)*\(?CUSIP Number", re.I)
THIRTEEN = ("SC 13G", "SC 13G/A", "SC 13D", "SC 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A", "SCHEDULE 13D", "SCHEDULE 13D/A")


def cusips(st, t_end):
    p = D / "cusips.json"
    have = json.loads(p.read_text()) if p.exists() else {}
    for cik in sorted(universe()):
        if cik in have or time.time() > t_end:
            continue
        codes, per_year = {}, {}
        try:
            for form, fd, acc, doc in filings(cik):
                if form in THIRTEEN and fd >= "2018-01-01" and per_year.get(fd[:4], 0) < 2:
                    per_year[fd[:4]] = per_year.get(fd[:4], 0) + 1
                    b = get(sec_url(cik, acc, doc), raw=True) or b""
                    m = CUSIP_XML.search(b) or CUSIP_TXT.search(b) or CUSIP_BEFORE.search(b)
                    if m and sum(ch.isdigit() for ch in m.group(0).decode("latin-1")[-12:]) >= 3:
                        c = m.group(1).decode().upper()
                        codes[c] = max(codes.get(c, ""), fd)
        except Exception as e:
            print(cik, "failed", str(e)[:100])
            continue
        have[cik] = sorted(codes, key=lambda c: codes[c])
        p.write_text(json.dumps(have))
    return have


def datasets():
    page = get("https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets", raw=True) or b""
    links = sorted(set(re.findall(rb'href="([^"]+form13f\.zip)"', page)))
    out = []
    for l in links:
        l = l.decode()
        m = re.search(r"(\d{4})q(\d)_form13f", l)
        if m and f"{m.group(1)}q{m.group(2)}" < FIRST:
            continue
        out.append("https://www.sec.gov" + l if l.startswith("/") else l)
    return out


def rows(z, name):
    n = next(x for x in z.namelist() if x.upper().endswith(name))
    with z.open(n) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, "utf-8", errors="replace"), delimiter="\t")


def read_set(url, issuer_to_cik):
    b = get(url, raw=True)
    z = zipfile.ZipFile(io.BytesIO(b))
    sub = {r["ACCESSION_NUMBER"]: r for r in rows(z, "SUBMISSION.TSV") if r.get("SUBMISSIONTYPE") == "13F-HR"}
    names = {r["ACCESSION_NUMBER"]: r.get("FILINGMANAGER_NAME", "") for r in rows(z, "COVERPAGE.TSV")}
    total, univ, pos = {}, {}, {}
    for r in rows(z, "INFOTABLE.TSV"):
        a = r["ACCESSION_NUMBER"]
        if a not in sub:
            continue
        try:
            v = float(r.get("VALUE") or 0)
        except ValueError:
            continue
        total[a] = total.get(a, 0) + v
        cik = issuer_to_cik.get((r.get("CUSIP") or "")[:6].upper())
        if cik and (r.get("SSHPRNAMTTYPE") or "").upper() == "SH" and not (r.get("PUTCALL") or "").strip():
            univ[a] = univ.get(a, 0) + v
            try:
                pos.setdefault(a, {})[cik] = pos.setdefault(a, {}).get(cik, 0) + float(r.get("SSHPRNAMT") or 0)
            except ValueError:
                pass
    out_f, out_p = [], []
    for a, u in univ.items():
        share = u / total[a] if total.get(a) else 0
        if share >= 0.5 and len(pos.get(a, {})) >= 5:
            s = sub[a]
            out_f.append([a, s.get("CIK", ""), names.get(a, ""), s.get("PERIODOFREPORT", ""), s.get("FILING_DATE", ""), round(share, 3), len(pos[a])])
            out_p += [[a, c, int(n)] for c, n in pos[a].items()]
    return out_f, out_p


def iso(d):
    """13F data sets write dates as 31-MAR-2024."""
    m = re.match(r"(\d{2})-([A-Z]{3})-(\d{4})", d or "")
    mon = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
    return f"{m.group(3)}-{mon.index(m.group(2)) + 1:02d}-{m.group(1)}" if m else d


def append(name, header, data):
    p = D / name
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=300)
    a = ap.parse_args()
    t_end = time.time() + a.minutes * 60
    D.mkdir(parents=True, exist_ok=True)
    st = json.loads(STATE.read_text()) if STATE.exists() else {"read": []}
    have = cusips(st, t_end)
    if len(have) < len(universe()):
        return print(f"CUSIPs: {len(have)} of {len(universe())} companies so far")
    issuer_to_cik = {c: cik for cik, cs in have.items() for c in cs}
    print(f"CUSIPs: {len(issuer_to_cik)} issuer codes for {sum(1 for v in have.values() if v)} companies")
    for url in datasets():
        if url in st["read"] or time.time() > t_end:
            continue
        f, p = read_set(url, issuer_to_cik)
        for r in f:
            r[3], r[4] = iso(r[3]), iso(r[4])
        append("filings.csv", ["accession", "filer_cik", "filer", "period", "filed", "universe_share", "positions"], f)
        append("positions.csv", ["accession", "cik", "shares"], p)
        st["read"].append(url)
        STATE.write_text(json.dumps(st, indent=1))
        print(f"{url.rsplit('/', 1)[-1]}: {len(f)} specialist filings, {len(p)} positions", flush=True)


if __name__ == "__main__":
    main()
