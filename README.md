# Crash Triage

When a biotech stock falls 50% or more in a day, did it deserve to? This repo scans US biotech daily closing prices
for one-day crashes (5-year history plus a daily update), reads each company's press release and filings, scores
five factors (safety, cash runway, value vs cash, insider buying, other programs) with cut-offs and weights fitted on
past crashes, and publishes a static site.

Data: SEC EDGAR, ClinicalTrials.gov, Twelve Data and Tiingo (daily closes). Runs on GitHub Actions; site on Cloudflare Pages.
