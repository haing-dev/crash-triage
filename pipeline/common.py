"""Shared helpers: polite HTTP with retries and per-host rate limits."""
import json, os, time, urllib.error, urllib.request

SEC_UA = os.environ.get("SEC_USER_AGENT", "crash-triage")
_last = {}
_MIN_GAP = {"sec": 0.13, "tiingo": 0.0, "twelvedata": 7.6, "ctgov": 0.2}   # seconds between calls per host


def _host_key(url):
    if "sec.gov" in url:
        return "sec"
    if "tiingo" in url:
        return "tiingo"
    if "twelvedata" in url:
        return "twelvedata"
    if "clinicaltrials" in url:
        return "ctgov"
    return "other"


def get(url, raw=False, tries=5, headers=None):
    key = _host_key(url)
    h = headers or ({"User-Agent": SEC_UA, "Accept-Encoding": "identity"} if key == "sec" else {"User-Agent": "crash-triage"})
    for i in range(tries):
        gap = _MIN_GAP.get(key, 0) - (time.time() - _last.get(key, 0))
        if gap > 0:
            time.sleep(gap)
        _last[key] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                data = r.read()
            return data if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(5 * (i + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < tries - 1:
                time.sleep(5 * (i + 1))
                continue
            raise
