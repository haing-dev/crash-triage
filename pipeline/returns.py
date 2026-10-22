"""
Prices, market value and returns, one implementation for every company and every page.

  stored closes  split-adjusted (Massive / Twelve Data: splits only; Tiingo, used for companies that stopped trading:
                 splits and dividends already folded in)
  actual close   the price that day before any later split (stored split list, or Tiingo's raw closes)
  market value   actual close x shares on the latest cover page filed by that day, carried through splits since
  total return   (close at the end + cash distributions per share with ex-date in between) / close at the start - 1,
                 all on the same split-adjusted basis. A reverse split is in the adjusted prices, so it neither helps
                 nor hurts the return; new shares a company sells show up in its share price. A company that stopped
                 trading (bought out, delisted) is valued at its last close: what a holder could have sold at.
  entry day      91 days after the crash/slide is detected: the call is made from everything public by then and the
                 stock bought at that close, held a year (backtest: buying then beat buying on detection, before and
                 after July 2023). A company no longer trading by then gets no call.
"""
import csv, json
from bisect import bisect_right
from datetime import date, timedelta
from functools import lru_cache
from common import DATA, closes, universe

BUY_DELAY = 91


def plus(d, n):
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def buy_day(entry):
    return plus(entry, BUY_DELAY)


@lru_cache(maxsize=None)
def splits(cik):
    p = DATA / "splits" / f"{cik}.json"
    return json.loads(p.read_text())["splits"] if p.exists() else None


@lru_cache(maxsize=None)
def raw_closes(cik):
    p = DATA / "prices_raw" / f"{cik}.csv"
    return {r["date"]: float(r["close"]) for r in csv.DictReader(p.open()) if r.get("close")} if p.exists() else {}


def at_or_before(cik, d):
    ds, cs = closes(cik)
    i = bisect_right(ds, d) - 1
    return (ds[i], cs[i]) if i >= 0 else (None, None)


def actual_close(cik, d):
    """Close on trading day d as it was quoted then (no adjustment for later splits)."""
    ds, cs = closes(cik)
    i = bisect_right(ds, d) - 1
    if i < 0 or ds[i] != d:
        return None
    sp = splits(cik)
    if sp is not None:
        px = cs[i]
        for day, num, den in sp:
            if day > d:
                px *= num / den
        return px
    return raw_closes(cik).get(d)


@lru_cache(maxsize=None)
def share_rows(cik):
    p = DATA / "shares" / f"{cik}.json"
    return json.loads(p.read_text())["rows"] if p.exists() else None


def shares_on(cik, d):
    """Shares on the latest cover page filed by day d (dated within 9 months), carried through any split since."""
    rows = share_rows(cik)
    if not rows:
        return None
    known = [r for r in rows if r[1] <= d]
    if not known:
        return None
    as_of, _, n = max(known, key=lambda r: (r[0], r[1]))
    if as_of < plus(d, -274):                             # no cover page in 9 months: share count unknown
        return None
    for day, num, den in splits(cik) or []:
        if as_of < day <= d:
            n *= num / den
    return n


def mcap(cik, d):
    px, n = actual_close(cik, d), shares_on(cik, d)
    return px * n if px and n else None


@lru_cache(maxsize=None)
def dividends(cik):
    """[(ex_date, amount per share on the stored split-adjusted basis)]; none added for Tiingo histories (their
    adjusted closes already include them)."""
    u = universe().get(cik, {})
    p = DATA / "market" / "dividends" / f"{cik}.csv"
    if u.get("price_source") == "tiingo" or not p.exists():
        return []
    ds, _ = closes(cik)
    out = []
    for r in csv.DictReader(p.open()):
        try:
            amt = float(r["cash_amount"])
        except (TypeError, ValueError):
            continue
        ex = r["ex_date"]
        if not ds or not ds[0] <= ex <= ds[-1] or amt <= 0:
            continue
        for day, num, den in splits(cik) or []:
            if day > ex:
                amt *= den / num
        out.append((ex, amt))
    return sorted(out)


def total_return(cik, d0, d1):
    """Bought at the close of trading day d0, valued at the last close on or before d1. Returns (return, last day)."""
    ds, cs = closes(cik)
    i0, i1 = bisect_right(ds, d0) - 1, bisect_right(ds, d1) - 1
    if i0 < 0 or i1 < i0 or cs[i0] <= 0:
        return None, None
    div = sum(a for ex, a in dividends(cik) if ds[i0] < ex <= ds[i1])
    return (cs[i1] + div) / cs[i0] - 1, ds[i1]


STEPS = list(range(0, 366, 5)) + ([365] if 365 % 5 else [])


def path(cik, d0):
    """Total return from d0 to each of STEPS days later (None if the year isn't complete)."""
    if plus(d0, 365) > date.today().isoformat():
        return None
    out = []
    for k in STEPS:
        r, _ = total_return(cik, d0, plus(d0, k))
        if r is None:
            return None
        out.append(r)
    return out


def daily(cik, d0, d1, calendar):
    """Day-by-day returns on the calendar days after d0 up to d1 (0 on days it didn't trade or after it stopped)."""
    ds, cs = closes(cik)
    divs = dict(dividends(cik))
    out, prev = {}, None
    for t in calendar:
        if t < d0 or t > d1:
            continue
        i = bisect_right(ds, t) - 1
        if i < 0:
            continue
        px = cs[i] + (divs.get(t, 0) if ds[i] == t else 0)
        if prev is not None and t > d0:
            out[t] = px / prev - 1 if prev > 0 else 0.0
        prev = cs[i]
    return out
