# NorthStar Churn Model: Monitoring and Reliability Runbook

## Drift Detection Plan

### 1. Drift types most likely for NorthStar

Data drift is the most likely type, because NorthStar's customers shop more in some periods than others and the business deliberately drives those swings. Holiday and seasonal promotions raise purchase frequency, order value and 90-day spend across most of the customer base for weeks at a time. A catalog refresh, which adds or retires product categories, changes how many categories a customer buys from and how large their baskets are, even when the customer's loyalty has not changed. Both events move the model's inputs immediately and recur every year. They also affect the features the model relies on most, since the five monitored features below are the five most important by XGBoost gain, so a shift in any of them moves churn scores directly.

Concept drift is the second most likely type. A promotion can change what a behavior means as well as how common it is. A customer whose 30-day purchase frequency rose because of a holiday sale is not necessarily more loyal, so the same feature values can carry a different churn risk during and after a promotion. This kind of drift develops more slowly, and it cannot be confirmed until the 90-day churn outcome is known.

Model degradation is the consequence of both, and it is what this plan ultimately protects against. It is measured as recall in the top 10% of scores once outcomes are known, which means it lags the prediction by 90 days.

Promotional periods are expected to set off the data drift alarm, and the alarm still pages when they do. The on-call engineer first checks the promotion and catalog calendar to see whether the drifted features and their timing match a scheduled promotion or catalog change, and then decides whether the model needs attention.

### 2. Statistical tests per feature

The baseline window is the training split of the currently deployed model. It contains the feature values computed as of that model's training feature cutoff for the 70% stratified training partition, exported with the 11 endpoint input columns to `monitoring/baseline/baseline.csv` and uploaded to `s3://<data-bucket>/monitoring/baseline/`. The baseline is replaced every time the model is retrained, so drift is always measured against the data the serving model learned from.

The comparison window is one weekly scoring cycle of requests captured from the endpoint, and it must contain at least 500 distinct records. When a window has fewer, the job marks it as underpowered and its result is not acted on.

The drift job reads the capture from the champion variant. The champion serves 90% of traffic and is the model in production, whereas a canary serves a small share of traffic for a short period and is judged during that time by its own rollback alarm.

The plan uses two tests. PSI (population stability index) splits a feature's values into bins, compares the share of customers in each bin between the baseline and the recent data, and sums those differences into a single number. A PSI of 0 means the two distributions are identical, and a larger value means a bigger change. It therefore measures how large a shift is, and it works the same way whether a feature's values are continuous or repeat often. The KS (Kolmogorov-Smirnov) test builds the cumulative distribution of each sample, finds the largest gap between the two curves, and reports a p-value, which is the probability of seeing a gap that large if nothing had changed. A small p-value indicates drift. KS answers whether the two distributions differ at all, and it is designed for continuous values, so its p-value becomes inexact when many customers share exactly the same value.

| Feature | Gain rank | Test | Threshold | Drift when | Why this test |
|---|---|---|---|---|---|
| `days_since_last_purchase` | 1 | PSI | 0.2 | PSI > 0.2 | The values are whole days and many customers share the same value, so PSI is the better fit. |
| `avg_order_value` | 2 | PSI | 0.2 | PSI > 0.2 | The values are continuous and either test would work, but during promotions the useful question is how far the level moved, which PSI reports as a size. |
| `total_spend_90d` | 3 | KS | 0.05 | p-value < 0.05 | The values are continuous with thousands of distinct amounts, which is the case KS is built for, and KS notices a change anywhere in the distribution, such as the top spenders spending more. |
| `category_diversity_score` | 4 | PSI | 0.2 | PSI > 0.2 | The feature takes only eight values (categories bought out of eight), so the KS p-value would be inexact and PSI is used instead. |
| `purchase_frequency_30d` | 5 | PSI | 0.2 | PSI > 0.2 | The feature is a small count with few distinct values and many zeros, so PSI is used for the same reason. |

The thresholds follow standard conventions. A PSI below 0.1 is usually read as stable, 0.1 to 0.2 as a moderate change, and above 0.2 as a significant shift, while 0.05 is the conventional significance level for the KS test. Because PSI rises with drift and the KS p-value falls with it, the two tests are compared against their thresholds in opposite directions. `drift_direction()` in `monitoring/evidently_drift.py` applies the correct comparison for each test, and `DRIFT_PLAN` in the same file holds exactly this table.

If any feature drifts, `monitoring/publish_metrics.py` publishes `DriftDetected = 1`, which triggers the `northstar-churn-drift-detected` alarm.

### 3. Concept drift detection before labels arrive

The churn label for a prediction becomes known only after the 90-day outcome window closes, so until then the plan uses the distribution of the model's own output scores as a proxy signal.

In each weekly scoring cycle, the distribution of churn scores in the captured predictions is compared using PSI with the distribution of scores the deployed model gives the baseline training split. Alongside this, the daily count of churn alerts (scores at or above 0.5, published as `ChurnAlertVolume`) is compared with the mean of the prior seven days, and the change is published as `ChurnAlertVolumeChangePct`. Both signals are available the same day, as soon as a scoring cycle's predictions are captured. The proxy fires when the score PSI exceeds 0.2 or when the daily churn-alert volume falls more than 30% below that seven-day mean, and the second condition is the `northstar-churn-alert-volume-drop` alarm.

This signal is useful because the retention program acts on the share of customers the model flags. If the score distribution moves, the population sent to retention changes before anyone knows whether those customers churn. Read together with the feature tests, the signal separates two cases. If the scores shift and the monitored features also drift, the cause is most likely data drift and is handled as described in section 2. If the scores shift and no monitored feature drifts, the change comes either from features or combinations of features that the tests do not cover, or from customers with the same profile behaving differently, and it is treated as suspected concept drift.

This approach cannot detect a change in churn risk that leaves both the inputs and the scores unchanged. That case is caught only by the labeled evaluation, which measures recall in the top 10% for the cohort scored 90 days earlier. Whenever the proxy fires, the labeled evaluation is run on the most recent cohort with known outcomes to confirm or rule out degradation.

## Service Level Objectives

All four SLOs are measured over a rolling 30-day window. The request-based budgets assume NorthStar's volume of about 2.1 million churn predictions a week, which comes to about 9.0 million predictions over 30 days (2.1 million × 30 / 7).

| SLO | Target | SLI (Good Events / Total Events) | Error Budget | Deployment Freeze Trigger |
|---|---|---|---|---|
| Availability | 99.5% | Successful predictions / total requests | 0.5% of requests, about 45,000 failed predictions per 30 days | More than 45,000 failed predictions in the last 30 days |
| Latency (p95) | < 20 ms (`ModelLatency` ≤ 20000) | Requests completing < 20 ms / total requests | 5% of requests, because the target is a 95th percentile, about 450,000 requests slower than 20 ms per 30 days | More than 450,000 requests slower than 20 ms in the last 30 days |
| Prediction Quality | Recall@10% ≥ 0.25 on weekly sample | Weekly sample passing threshold / total weekly samples | 1 failed weekly sample per 30 days | A second failed weekly sample within the last 30 days |
| Fairness | Recall gap across loyalty tiers ≤ 10pp | Weeks within fairness threshold / total weeks | 0 failed weeks per 30 days | Any weekly sample with a recall gap above 10 percentage points |

When a freeze trigger fires, all deployments stop except rollbacks and changes that fix the breached SLO. The freeze lifts once the budget spent over the last 30 days is back within its limit. Deployments themselves also spend latency budget, because each one has a cold start of about 24 ms.

Prediction quality is allowed one failed week because recall in the top 10% of a single weekly sample is noisy, so one miss may be sampling variation rather than a worse model. Fairness has no budget at all, because a recall gap between loyalty tiers means the retention program is treating customer groups differently. That is a governance commitment rather than a performance target.

Each SLI is measured as follows:

- Availability is one minus the sum of `Invocation5XXErrors` divided by the sum of `Invocations` in the `AWS/SageMaker` namespace, taken across both variants. 4XX responses are caller errors, so they are not counted as failures.
- Latency is the `PR(:20000)` statistic on `ModelLatency` in `AWS/SageMaker`, which reports the share of requests that completed in 20000 microseconds or less, weighted by each variant's invocations.
- Prediction quality is recall in the top 10% of scores for the weekly cohort scored 90 days earlier, whose churn outcomes are now known.
- Fairness is recall in the top 10% computed separately for Bronze, Silver, Gold and Platinum customers on the same weekly sample, and the gap is the highest tier's recall minus the lowest tier's.
