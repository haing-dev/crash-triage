# Smoke test
run: 2026-10-14 16:06 UTC

- **Tiingo · delisted coverage in ticker list**: OK — 3,952 US stocks that stopped trading Oct 2021–Aug 2026; {'SGEN': {'exchange': 'NASDAQ', 'startDate': '2001-03-09', 'endDate': '2026-10-13'}, 'MRTX': {'exchange': 'NASDAQ', 'startDate': '2013-07-15', 'endDate': '2026-10-13'}, 'KRTX': {'exchange': 'NASDAQ', 'startDate': '2019-06-28', 'endDate': '2026-10-13'}, 'CERE': {'exchange': 'NASDAQ', 'startDate': '2012-02-22', 'endDate': '2016-08-02'}, 'ATHX': None, 'CLVSQ': {'exchange': 'PINK', 'startDate': '2011-11-16', 'endDate': '2023-07-25'}}
- **Tiingo · acquired/delisted SGEN**: OK — 140 daily closes, 2023-06-01 → 2023-12-19
- **Tiingo · acquired/delisted MRTX**: OK — 162 daily closes, 2023-06-01 → 2024-01-23
- **Tiingo · acquired/delisted KRTX**: OK — 200 daily closes, 2023-06-01 → 2024-03-18
- **Tiingo · acquired/delisted CERE**: OK — 147 daily closes, 2024-01-02 → 2024-08-01
- **Tiingo · acquired/delisted ATHX**: OK — 290 daily closes, 2023-01-03 → 2024-02-28
- **Tiingo · acquired/delisted CLVSQ**: OK — 272 daily closes, 2022-06-01 → 2023-06-30
- **Twelve Data · acquired SGEN (2023)**: FAIL — HTTP Error 404: Not Found
- **Twelve Data · listed stock (VRTX)**: OK — 5000 daily closes, 2006-11-28 → 2026-10-14
- **Twelve Data · delisted stock (CLVS, 2022)**: FAIL — HTTP Error 404: Not Found
- **Tiingo · listed stock (VRTX)**: OK — 30 daily closes, 2026-09-01 → 2026-10-13
- **Tiingo · delisted stock (CLVS, 2022)**: OK — 0 rows
- **Tiingo · full ticker list incl. delisted**: OK — 109,005 tickers listed (16,234 US stocks); CLVS entry: {'ticker': 'CLVS', 'exchange': 'NASDAQ', 'assetType': 'Stock', 'priceCurrency': 'USD', 'startDate': '2023-01-03', 'endDate': '2023-01-03'}
- **SEC EDGAR · filings + XBRL (Vertex)**: OK — VERTEX PHARMACEUTICALS INC / MA · SIC 2834 (Pharmaceutical Preparations) · 264 cash data points via XBRL
- **ClinicalTrials.gov API v2**: OK — 296 Vertex-sponsored studies; e.g. NCT03691779
