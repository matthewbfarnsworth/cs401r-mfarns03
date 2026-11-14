# Lab 5 Batch Comparison: incumbent v7 vs candidate v8

Run `20261114-010904`, generated 2026-11-14T20:59:47Z by `scripts/churn-batch-compare.sh`. The numbers are produced by the script; re-run it rather than editing them.

## Jobs

| Role | Model package | Transform job |
|---|---|---|
| Incumbent | northstar-churn-models/7 | `northstar-churn-batch-incumbent-v7-20261114-010904` |
| Candidate | northstar-churn-models/8 | `northstar-churn-batch-candidate-v8-20261114-010904` |

Customers scored by both models, matched by `customer_id`: **9999**

## Score distributions

| Statistic | v7 | v8 |
|---|---|---|
| min | 0.0266 | 0.0220 |
| 10th percentile | 0.1137 | 0.1082 |
| 25th percentile | 0.2218 | 0.2316 |
| median | 0.4034 | 0.4130 |
| 75th percentile | 0.5537 | 0.5538 |
| 90th percentile | 0.6636 | 0.6573 |
| max | 0.9768 | 0.9806 |
| mean | 0.4075 | 0.4106 |

## Score histogram (customers per score band)

| Score band | v7 | v8 |
|---|---|---|
| 0.0 to 0.1 | 844 | 915 |
| 0.1 to 0.2 | 1369 | 1215 |
| 0.2 to 0.3 | 1274 | 1231 |
| 0.3 to 0.4 | 1463 | 1398 |
| 0.4 to 0.5 | 1633 | 1805 |
| 0.5 to 0.6 | 1671 | 1689 |
| 0.6 to 0.7 | 952 | 979 |
| 0.7 to 0.8 | 151 | 112 |
| 0.8 to 0.9 | 80 | 91 |
| 0.9 to 1.0 | 562 | 564 |

## Disagreement at the 0.5 churn cut-off

A customer is predicted to churn when their score is 0.5 or higher. The models disagree on a customer when exactly one of them predicts churn.

| Measure | Customers | Share |
|---|---|---|
| Predicted churn by v7 | 3416 | 34.16% |
| Predicted churn by v8 | 3435 | 34.35% |
| Churn by v7 only | 489 | |
| Churn by v8 only | 508 | |
| **Disagreement** | **997** | **9.97%** |

## Rollback trigger

Defined in `deployment/configs/rollback-alarm.json`: `PredictionDisagreementPct` > 5 (Percent). This run published 9.97.

**Result: BREACHED.** Roll back with `bash deployment/configs/rollback-action.sh 8`.
