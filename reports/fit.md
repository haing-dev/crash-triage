# Fit report
crashes with factor inputs: 960 · with a full year: 796

## Model on all history (what new crashes get)

| Factor | Cut-off | Points green / grey / red (pp vs median) |
|---|---|---|
| safety | categorical | +0.0 / +0.0 / +0.0 |
| runway | ≥ 6.11 is green | +20.1 / +10.6 / -24.1 |
| value | not enough data | +0.0 / +0.0 / +0.0 |
| insider | categorical | +14.1 / -5.3 / +10.1 |
| programs | ≥ 1.00 is green | +7.7 / +0.0 / -8.6 |

## Out-of-sample check (walk-forward: verdict given at the time, outcome vs XBI)

| Verdict | n | median stock move | median vs XBI | beat XBI | up 50%+ | down 80%+ |
|---|---|---|---|---|---|---|
| overdone | 105 | -11% | -39% | 40% | 31% | 10% |
| close | 74 | -63% | -66% | 19% | 7% | 38% |
| justified | 204 | -71% | -88% | 18% | 13% | 44% |
