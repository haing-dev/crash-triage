# Smoke test
run: 2026-10-14 15:59 UTC

- **Twelve Data · listed stock (VRTX)**: OK — 5000 daily closes, 2006-11-28 → 2026-10-14
- **Twelve Data · delisted stock (CLVS, 2022)**: FAIL — HTTP Error 404: Not Found
- **Tiingo · listed stock (VRTX)**: OK — 30 daily closes, 2026-09-01 → 2026-10-13
- **Tiingo · delisted stock (CLVS, 2022)**: OK — 0 rows
- **Tiingo · full ticker list incl. delisted**: OK — 109,005 tickers listed (16,234 US stocks); CLVS entry: {'ticker': 'CLVS', 'exchange': 'NASDAQ', 'assetType': 'Stock', 'priceCurrency': 'USD', 'startDate': '2023-01-03', 'endDate': '2023-01-03'}
- **SEC EDGAR · filings + XBRL (Vertex)**: OK — VERTEX PHARMACEUTICALS INC / MA · SIC 2834 (Pharmaceutical Preparations) · 264 cash data points via XBRL
- **ClinicalTrials.gov API v2**: OK — 296 Vertex-sponsored studies; e.g. NCT03691779
- **Claude Code · subscription token**: OK — Claude Code replied 'ok'
