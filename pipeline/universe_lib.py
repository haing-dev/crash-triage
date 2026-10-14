import csv, io, zipfile
from common import get

US_EXCH = {"NASDAQ", "NYSE", "NYSE MKT", "NYSE ARCA", "AMEX", "BATS"}


def tiingo_list():
    z = zipfile.ZipFile(io.BytesIO(get("https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip", raw=True)))
    tl = {}
    for r in csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]), "utf-8")):
        if r.get("assetType") == "Stock" and r.get("priceCurrency") == "USD" and r.get("startDate"):
            tl.setdefault(r["ticker"].upper(), []).append(r)
    return tl


def tiingo_match(tl, tick, first, last):
    for t in (tick, tick + "Q", tick + "F"):
        for r in tl.get(t, []):
            if r["startDate"] <= last and (r["endDate"] or "9999") >= first[:7]:
                return t, r
    return None, None
