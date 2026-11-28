# NorthStar Value Scorecard

## 1. Metric Pyramid

The second system is Track C, the customer service agent. It was deployed during development and has since been torn down.

Churn metrics assume the 2.1M active customers are scored weekly, as the case overview states. Reference values from the handout appear in parentheses where it publishes one. A deployment freeze means all deployments stop except rollbacks and fixes.

A link is Validated when both metrics come from the same population over the same window. Both must be produced by a pipeline or repeatable evaluation harness that already exists in the platform's code, even if it is not deployed right now. Every other link is Assumed. Each metric in the lower three layers drives one metric in the layer above. Top-layer metrics with no metric beneath them are tracked as outcomes only.

Several churn decision rules fall back to the rule-based risk score, the fixed recency rule the model is compared against.

### 1.1 Churn Prediction

```text
Incremental 90-day retention               (Business Outcome)
├── Campaign click-through                 (User Experience)
│   └── At-risk list size                  (Model Output)
│       ├── Recall@10%                     (Model / System)
│       └── Inference latency, p95         (Model / System)
└── Offer redemption rate                  (User Experience)
    └── Score distribution shift           (Model Output)
        └── Recall gap across loyalty tiers (Model / System)

Annual churn rate                          (Business Outcome)
Prevented churn revenue                    (Business Outcome, computed from incremental 90-day retention)
```

#### Model / System

| Metric | Calculation | Owner | Frequency | Decision rule | Current value |
|---|---|---|---|---|---|
| Recall@10% | Churners in the top 10% of scores divided by all churners. It is computed on the weekly cohort scored 90 days earlier, once its 90-day labels exist | Chief Data Officer (Maya Chen) | Weekly | If two weekly samples with complete 90-day labels fall below 0.25 within 30 days of each other, freeze deployments. Build the weekly list from the rule-based risk score until a retrained model passes | 0.3212 on the held-out test split (ref. 0.3106) |
| Inference latency, p95 | 95th percentile of `ModelLatency` (microseconds) across scoring requests | VP of Technology (James Wu) | Continuous, over a rolling 30 days | If more than 5% of requests in the last 30 days take over 20 ms (`ModelLatency` above 20,000 µs), freeze deployments | 3.2 ms on a test replay (ref. 4.1 ms) |
| Recall gap across loyalty tiers | Largest Recall@10% difference between any two loyalty tiers | Chief Privacy Officer (Sarah Okafor) | Weekly | If the gap exceeds 10 percentage points in any weekly sample, freeze deployments. Also hold the affected tiers' contacts until the Chief Privacy Officer reviews them. This contact hold adds to the deployment freeze that the fairness service level objective (SLO) already sets | 34 percentage points, Bronze 0.43 vs. Platinum 0.10, on the held-out test split |

#### Model Output

| Metric | Calculation | Owner | Frequency | Decision rule | Current value |
|---|---|---|---|---|---|
| At-risk list size | Customers scoring 0.5 or higher in the weekly scoring run. This is a model-health count. It is separate from the campaign's contact list, which is always the top 10% of scores | VP of Marketing (David Park) | Weekly | If the count falls more than 30% below the previous week's run, build that week's list from the rule-based risk score. Do not switch if a scheduled promotion or catalog change explains the drop | 346 on a test replay |
| Score distribution shift | Population stability index (PSI) of the week's scores against the deployed model's scores on the training baseline | Chief Data Officer (Maya Chen) | Weekly | If PSI exceeds 0.2, run the labelled evaluation on the latest cohort with known outcomes. If a scheduled promotion or catalog change accounts for the shift, record it as explained. Otherwise build the weekly list from the rule-based risk score until the model is retrained or the shift is explained | Not yet measured |

#### User Experience

| Metric | Calculation | Owner | Frequency | Decision rule | Current value |
|---|---|---|---|---|---|
| Campaign click-through | Contacted customers who click the retention message divided by contacted customers whose message was delivered | VP of Marketing (David Park) | Weekly, per campaign | If click-through falls more than 30% below the previous weekly campaign, check email delivery and click tracking before the next send | Not yet measured |
| Offer redemption rate | Offers redeemed within their validity period divided by offers delivered to at-risk customers | VP of Marketing (David Park) | Weekly, per campaign | If a campaign's redemption is below the 6% pre-AI baseline, revise the offer's amount, product and timing before the next campaign | Not yet measured (case baseline 6%) |

#### Business Outcome

| Metric | Calculation | Owner | Frequency | Decision rule | Current value |
|---|---|---|---|---|---|
| Incremental 90-day retention | 90-day retention of contacted customers minus that of a random 10% of the same week's list who get no contact | VP of Marketing (David Park) | Weekly, each cohort read 90 days after scoring | If the difference is zero or negative for three cohorts in a row, build the weekly list from the rule-based risk score | Not yet measured |
| Annual churn rate | Customers active 12 months ago with no purchase in the last 12 months, divided by customers active 12 months ago. This is the case overview's 18% definition, not the model's 90-day label. The two figures are never mixed | Chief Data Officer (Maya Chen) | Quarterly, trailing 12 months | If the rate misses a quarterly checkpoint on the straight-line path to the target (17%, 16%, 15% and 14% at the end of quarters one to four), the Chief Data Officer reports to the CFO with a revised target | 18% (case baseline) |
| Prevented churn revenue | Incremental retained customers, from incremental 90-day retention, times the $340 lifetime value. This assumes a customer retained through the 90-day window keeps the full $340 lifetime value | CFO (Robert Hess) | Quarterly | If prevented revenue is below the platform cost plus the retention offer cost for two quarters in a row, recommend redesign or decommissioning | Not yet measured |

#### Causal Links

| Link | Label | Basis | Validating experiment |
|---|---|---|---|
| Recall@10% -> At-risk list size | Assumed | When the model ranks churners higher, more of them score 0.5 or higher. Recall@10% is computed only on the held-out test split. List size is computed on live scoring traffic | For 12 weekly cohorts, score a random half of customers with the current model and the other half with the prior approved registry version, which has lower recall. Compare each half's list size, then each half's Recall@10% once 90-day labels arrive |
| Inference latency, p95 -> At-risk list size | Validated | CloudWatch records both metrics from the same captured scoring traffic: `ModelLatency` per request, and list size as the `ChurnAlertVolume` custom metric. The link uses the p95 of `ModelLatency` over the same weekly scoring run that produces the list-size count | None needed |
| Recall gap across loyalty tiers -> Score distribution shift | Assumed | A model that under-ranks a tier's churners pushes that tier's scores closer together, which shifts the overall score distribution. The tier recall gap is computed only on the held-out test split. The score shift is computed on live scoring traffic | For 12 weekly cohorts, score a random half of customers with a challenger trained with tier-balanced sample weights and the other half with the current model. Compare each half's PSI, then each half's tier recall gap once 90-day labels arrive |
| At-risk list size -> Campaign click-through | Assumed | Weeks with more customers at 0.5 or higher fill the top-10% contact list with higher-risk customers, who respond to outreach differently. The platform does not measure click-through, and no job joins campaign results to scores | Each week, contact a random half of the customers ranked just below the top-10% cut-off. Compare their click-through with the top 10% over 8 campaigns |
| Score distribution shift -> Offer redemption rate | Assumed | A shifted score distribution changes who makes the contact list, and so changes the redemption rate. The platform does not measure redemption | For 12 campaigns, build a random half of each week's contact list from the prior approved registry version's scores, which have a different distribution. Build the other half from the current model's scores. Compare redemption between the halves |
| Campaign click-through -> Incremental 90-day retention | Assumed | Customers who engage with the outreach are retained more than the holdout. Neither metric is measured, and retention needs the holdout | For one quarter, randomly send contacted customers either the standard message or a version with one prominent offer button meant to raise click-through. Compare each group's 90-day retention against the 10% holdout |
| Offer redemption rate -> Incremental 90-day retention | Assumed | Redeeming the offer keeps customers who would otherwise churn. Neither metric is measured, and redemption is the customer's own choice | For one quarter, randomly send each contacted customer either the offer or a contact with no offer. Compare 90-day retention between the two groups |

### 1.2 Customer Service Agent (Track C)

```text
Routine contact automation rate            (Business Outcome)
└── Customer satisfaction (CSAT) for AI-handled contacts (User Experience)
    └── Escalation correctness             (Model Output)
        └── Task success rate              (Model / System)

Cost per contact                           (Business Outcome)
└── Seven-day repeat contact rate          (User Experience)
    └── Escalation rate                    (Model Output)
        ├── Response latency, p95          (Model / System)
        └── Availability during business hours (Model / System)

Handle time for escalated contacts         (Business Outcome)
```

#### Model / System

| Metric | Calculation | Owner | Frequency | Decision rule | Current value |
|---|---|---|---|---|---|
| Task success rate | Evaluation scenarios where the agent called every required tool and no forbidden tool, divided by scenarios run | Chief Data Officer (Maya Chen) | Every release candidate | If any scenario fails, block promotion of the release candidate | 100%, 12 of 12 scripted scenarios across two runs |
| Response latency, p95 | 95th percentile of end-to-end seconds per agent turn | VP of Technology (James Wu) | Per business day | If p95 exceeds 10 seconds on a business day, investigate model and tool latency. Fix or replace the slowest step | 8.3 s across 18 scripted sessions |
| Availability during business hours | Minutes the agent answered successfully divided by business-hours minutes (8 AM to 10 PM in all time zones) | VP of Technology (James Wu) | Monthly | If availability falls below 99.5% in a month, freeze changes except fixes | Not yet measured |

#### Model Output

| Metric | Calculation | Owner | Frequency | Decision rule | Current value |
|---|---|---|---|---|---|
| Escalation rate | Routine contacts the agent hands to a human, divided by routine contacts it receives | VP of Customer Experience (Priya Nair) | Weekly | If more than 50% of routine contacts are escalated in a week, review the routing prompt and tool coverage | 0%, 0 of 4 routine scripted scenarios |
| Escalation correctness | Resolve or escalate decisions that match a human reviewer's judgement, on an audit of 100 sampled contacts | VP of Customer Experience (Priya Nair) | Weekly | If correctness falls below 95%, review the failed cases and fix the escalation rules involved | 100%, 12 of 12 scripted scenarios |

#### User Experience

| Metric | Calculation | Owner | Frequency | Decision rule | Current value |
|---|---|---|---|---|---|
| CSAT for AI-handled contacts | Share of post-contact survey responses scoring 4 or 5 out of 5 on contacts the agent resolved | VP of Customer Experience (Priya Nair) | Weekly | If AI CSAT is more than 5 points below human-handled CSAT in a week, review a sample of low-scoring transcripts. Change the prompt or tools for the contact types the review finds | Not yet measured |
| Seven-day repeat contact rate | AI-resolved contacts followed within seven days by another contact from the same customer on the same issue, divided by AI-resolved contacts | VP of Customer Experience (Priya Nair) | Weekly | If the rate rises more than 30% over the previous week, review transcripts for the repeated issue types. Fix the agent's answers or tools | Not yet measured |

#### Business Outcome

| Metric | Calculation | Owner | Frequency | Decision rule | Current value |
|---|---|---|---|---|---|
| Routine contact automation rate | Routine contacts the agent resolves without a human, divided by all routine contacts (target 50%) | VP of Customer Experience (Priya Nair) | Quarterly | If the rate misses a quarterly checkpoint on the straight-line path to the target (12.5%, 25%, 37.5% and 50% at the end of quarters one to four), re-examine the escalation rules in case the agent escalates too cautiously | Not yet measured |
| Handle time for escalated contacts | Reduction in average human handle time on escalated contacts against the pre-AI average (target 30%) | VP of Customer Experience (Priya Nair) | Quarterly | If the reduction misses a quarterly checkpoint on the straight-line path to the target (7.5%, 15%, 22.5% and 30% at the end of quarters one to four), revise the hand-off summary the agent passes to human agents | Not yet measured |
| Cost per contact | Agent model and runtime cost per AI-handled contact, compared with fully loaded human cost per contact | CFO (Robert Hess) | Quarterly | If AI cost per contact exceeds human cost per contact, limit the agent to the contact types where it is cheaper | $0.0095 estimated agent cost per scripted session; human cost per contact not yet measured |

#### Causal Links

| Link | Label | Basis | Validating experiment |
|---|---|---|---|
| Task success rate -> Escalation correctness | Assumed | An agent that calls the right tools has the information it needs to route correctly. Task success is measured on scripted scenarios. Escalation correctness is measured on an audit of live contacts | For 2 weeks, randomly route half of live contacts to the current agent and half to a release candidate with lower task success on the scripted suite. Compare escalation correctness on a 100-contact audit from each half |
| Response latency, p95 -> Escalation rate | Assumed | Slow turns make customers abandon the agent or time out and are handed to a human. No pipeline relates per-contact latency to the escalation decision | For 2 weeks, add a 3-second delay to each turn for a random 5% of routine contacts. Compare their escalation rate with the other contacts |
| Availability during business hours -> Escalation rate | Assumed | While the agent is down, every contact goes to a human. Outage windows are not joined to contact-center routing | For one quarter, run scheduled 30-minute failover drills at randomly chosen business-hour times. Compare the escalation rate during drills with the same windows on other days |
| Escalation rate -> Seven-day repeat contact rate | Assumed | Escalating fewer contacts leaves more weak resolutions that customers come back about. Repeat contacts are not tracked | For 4 weeks, assign a random half of routine contacts to a stricter escalation rule. Compare repeat contact rates between the two groups |
| Escalation correctness -> CSAT for AI-handled contacts | Assumed | Correctly routed contacts leave customers more satisfied. CSAT is not collected for agent contacts | In the same 4-week stricter-escalation test, which changes correctness, compare audit correctness and CSAT between the two groups |
| CSAT for AI-handled contacts -> Routine contact automation rate | Assumed | Unhappy customers ask for a human on their next contact instead of using the agent. Neither metric is measured | For 4 weeks, randomly assign contacts to the current prompt or a variant meant to raise CSAT. Compare the share of each group's next routine contacts that the agent resolves without a human |
| Seven-day repeat contact rate -> Cost per contact | Assumed | Each repeat contact is another contact to pay for, so a higher repeat rate raises cost per resolved issue. Neither metric is measured | In the same 4-week stricter-escalation test, which changes repeat contacts, compare cost per resolved issue between the two groups, counting repeat contacts |

## 2. Unit Economics

Every figure is a row in `docs/lab7-cost-model.csv`. Usage comes from Cost Explorer UsageQuantity (October and November 2026) and job logs; dollars are usage × rate, not the invoice.

### 2a. Rate card

Price List API, pulled 2026-11-28 UTC, one call per resource (usagetype varies); all seven below have effectiveDate 2026-11-01.

```bash
aws pricing get-products --service-code AmazonSageMaker --region us-east-1 \
  --filters 'Type=TERM_MATCH,Field=regionCode,Value=us-east-1' \
            'Type=TERM_MATCH,Field=usagetype,Value=USE1-Host:ml.t2.medium' \
  --query 'PriceList' --output json
```

Each row below uses this same command with that row's service code and usagetype, for example `--service-code AWSGlue` with `Value=USE1-ETL-DPU-Hour`, or `--service-code AmazonCloudWatch` with `Value=CW:MetricMonitorUsage`.

| Resource | Service code | usagetype | Rate (USD) | `description` returned |
|---|---|---|---|---|
| SageMaker hosting, ml.t2.medium | AmazonSageMaker | `USE1-Host:ml.t2.medium` | 0.056 per hour | $0.056 per Hosting ml.t2.medium hour in US East (N. Virginia) |
| SageMaker training, ml.g5.xlarge | AmazonSageMaker | `USE1-Train:ml.g5.xlarge` | 1.408 per hour | $1.408 per Training ml.g5.xlarge hour in US East (N. Virginia) |
| Feature Store read request units | AmazonSageMaker | `USE1-FeatureStore:ReadRequestUnits` | 0.00000025 per unit | $0.25 per million SageMaker Feature Store read request units in US East (N. Virginia) |
| Glue ETL job | AWSGlue | `USE1-ETL-DPU-Hour` | 0.44 per DPU-hour | $0.44 per Data Processing Unit-Hour for AWS Glue ETL job |
| Glue crawler | AWSGlue | `USE1-Crawler-DPU-Hour` | 0.44 per DPU-hour | $0.44 per Data Processing Unit-Hour for AWS Glue crawler |
| SageMaker batch transform, ml.m5.large | AmazonSageMaker | `USE1-Tsform:ml.m5.large` | 0.115 per hour | $0.115 per BatchTransform Job ml.m5.large hour in US East (N. Virginia) |
| CloudWatch custom metric | AmazonCloudWatch | `CW:MetricMonitorUsage` | 0.30 per metric-month | $0.30 per metric-month for the first 10,000 metrics - US East (Northern Virginia) |

- CloudWatch: with 15 custom metrics, the $0.30 first-10,000 tier applies, not the $0.02 over-1,000,000 tier.
- Bedrock: the rate comes from the Bedrock pricing page, read on 2026-11-28 UTC, because it is not in the Price List API. It is $1.10 per 1M input tokens and $5.50 per 1M output tokens for Claude Haiku 4.5 in US East (N. Virginia), and the agent uses the `us.` geo profile.
- The full raw output for every rate is in `docs/lab7-rate-card.txt`.

### 2b. Cost per 1,000 predictions (churn)

A month is 365 / 12 = 30.4167 days or 4.3452 weeks; products use the unrounded values.

| Scale input | Calculation | Value |
|---|---|---|
| Predictions per month | 2,100,000 customers × 30.4167 days | 63,875,000 (63,875 thousand) |
| NorthStar transactions per year | 72% × $3.2B / $87 store AOV + 28% × $3.2B / $112 online AOV | 26.48M + 8.00M = 34.48M |
| Lab dataset (own measured data, not the handout's 19,500 rows) | 163,255 transactions, 2025-04-01 to 2026-06-30 (15 months) | about 130,600 per year |
| Full-history factor | 34.48M / 130,600 | about 264× |
| Weekly incremental run | 34.48M / 52 = 663,077 transactions, / 163,255 | 4.06× one measured pass, applied to Glue below |

Bridging assumption: the sampled dataset is not a random draw from NorthStar's customers, so costs that grow with customers (training, Feature Store) scale by 2.1M / 10,000 = 210×, and costs that grow with transactions (Glue, S3) scale by the case's 34.48M a year.

| Component | Calculation | Monthly ($) | Per 1,000 ($) |
|---|---|---|---|
| Inference | 2 × ml.t2.medium (99.5% availability SLO) × 730 hr × $0.056 | 81.76 | 0.00128 |
| Amortized training | Per run: 0.02477 hr per 10,000 customers × 210 = 5.20 hr, × $1.408 = $7.32<br>Monthly: $7.32 × 4.3452 runs<br>Per 1,000: $7.32 / 14,700 thousand predictions per model (2.1M × 7 days) | 31.82 | 0.000498 |
| Feature Store reads | 63,875,000 reads × $0.25 / 1M (one unit per read, records under 4 KB) | 15.97 | 0.000250 |
| Data pipeline, churn share | ETL: 574 to 594 DPU-s × 4.06 / 3,600<br>Crawler: 0.0784 to 0.1269 DPU-hr × 4.06<br>Monthly cost: both × $0.44 × 4.3452 = $1.847 to $2.267<br>Churn share: × 50%, since the pipeline is shared with offer generation | 0.92 to 1.13 | 0.000014 to 0.000018 |
| Total | | 130.48 to 130.69 | 0.00204 to 0.00205 |

- Training is not free. It is a billed SageMaker job, and its time is assumed to grow linearly with the number of rows.
- Glue time is also assumed to grow linearly with transactions. This slightly overstates cost because part of each run is fixed startup time.
- The pipeline share is near zero because each weekly run covers only one week of new transactions, and that cost is split across two models.
- The measured runs were noisy: ETL passes differ by 3.5% and crawler runs by 62%. That moves the result by only $0.00001 per 1,000 predictions, so the conclusion does not depend on it.

The churn model costs $0.002 per 1,000 predictions ($0.00204 to $0.00205); the endpoint is 63% of it.

### 2c. Total platform cost (all three systems)

This table uses the high end of the 2b pipeline range, and the CSV lists every line item. The six categories sum to the total, and `derived_not_in_total` rows are excluded.

| Category | Monthly cost ($) | Assumption |
|---|---|---|
| Compute (training) | 31.82 | Churn weekly retrain; the two LLM systems use hosted Claude Haiku 4.5, no training |
| Compute (inference) | 1,846.70 | Churn endpoint; AgentCore for 425,833 sessions (14,000 contacts/day) and 912,500 offers (top 10% weekly), measured per-session use. Runtime per session is averaged over all 74 invocations Cost Explorer recorded, while tokens per session (below) are the mean of the 18 sessions in the agent cost log |
| Data pipeline, storage and transfer | 62.70 | Glue (counted here only), Feature Store, S3, NAT gateway hours |
| Third-party APIs (Bedrock) | 8,263.27 | 4,188 input / 285 output tokens per session (mean of 18 measured), same for offers |
| Human labor ($80/hr) | 9,892.80 | 123.66 hr/month of review, audit and on-call |
| Platform and tooling | 7.63 | CloudWatch alarms and metrics, CodeBuild, CodePipeline, drift job, KMS. The serverless MLflow App costs $0, which avoids the MLflow Tracking Server at $438/month ($0.60/hr × 730) |
| Total | 20,104.91 | Sum of unrounded rows [1] |

[1] Category figures are rounded to the cent, so their sum can differ from the total by one cent.

The total is 23.65% of the $85,000/month budget, leaving $64,895.09. Labor (49%) and Bedrock (41%) dominate and churn ($130.69) is under 1%, so token volume, LLM traffic and staffing drive cost, not churn serving.

### 2d. One cost optimization

Replace the churn real-time endpoint (2 × ml.t2.medium, $81.76/month) with one daily batch transform on ml.m5.large. Measured 9,999 customers in 114 s plus 171 s startup; assumes linear scaling.

| Step | Calculation | Result |
|---|---|---|
| Compute per day | 114 s × 2,100,000 / 9,999 | 23,942 s |
| Billed time per day | 23,942 s + 171 s startup = 24,113 s | 6.698 hr |
| Billed time per month | 6.698 hr × 30.4167 days | 203.74 hr |
| Optimized cost | 203.74 hr × $0.115 | $23.43/month |
| Saving | $81.76 − $23.43 | $58.33/month (71.3%) |
| Inference per 1,000 predictions | $23.43 / 63,875 | $0.00037, down from $0.00128 |

Only the inference saving is claimed. A measured failed job ran 35 minutes and billed about $0.067. A failure delays that day's scores, and the fallback is the previous day's scores.

| SLO | Target | Survives? | Why |
|---|---|---|---|
| Availability | 99.5% of requests | No | No requests; replace with a daily delivery deadline |
| Latency | p95 < 20 ms | No | No per-request latency; scores up to 24 hr old |
| Prediction quality | Recall@10% ≥ 0.25, weekly sample | Yes | Same model and features |
| Fairness | Recall gap across loyalty tiers ≤ 10 pp | Yes | Same model and features |

The SLO error budgets were sized for 2.1M predictions a week. At 2.1M scored daily the percentage targets still hold, but the request counts in each budget would be recomputed, since they are about 7× larger.

Tradeoff: no fresh single-customer score between runs, and two SLOs must be rewritten before shipping.

## 3. Executive Value Scorecard

### NorthStar AI Platform — Q4 2026 Value Scorecard

### Platform Summary

NorthStar loses 18% of its 2.1M active customers a year, about $128.5M in lifetime value (2.1M × 18% × $340). A churn system flags each week the customers most likely to stop buying, so marketing can send them retention offers, and a customer service agent is built to answer routine contacts so staff handle the rest.

### Systems in Production

| System | Business Metric | Current Performance | vs. Target | Status |
|---|---|---|---|---|
| Churn Prediction | Annual churn rate | 18%. No measured change yet; results will come from the retention holdout (a randomly chosen group that gets no offer) | Target 14% within a year. Not reachable on current evidence (see below) | Watch |
| Customer Service Agent | Routine automation rate (share of routine contacts resolved without a person) | Not deployed. In testing it resolved all 4 routine test cases without escalating | Target 50% of routine contacts | Watch |

### Can We Hit the Stated Target?

Verdict: the 14% target is not reachable within a year. It needs 69% of contacted leavers won back. The best result a 2018 published field study (Ascarza, below) reports is 16%; offers to the customers most likely to leave won back under 2%.

| Step | Calculation | Result |
|---|---|---|
| Customers who must be kept each year | (18% − 14%) × 2.1M | 84,000 |
| Customers who will leave in a year | 18% × 2.1M | 378,000 |
| Leavers inside the contacted group | Our system places 32% of customers who leave into the top 10% it flags: 0.3212 × 378,000 | 121,414 |
| Share of those leavers the offer must win back | 84,000 / 121,414 | 69% |

The 32% came from a 10,000-customer sample over 90 days. This assumes it holds for a full year across all 2.1M customers, and treats a year's weekly contact lists as one top-10% group of 210,000.

Ascarza, E. (2018), "Retention Futility: Targeting High-Risk Customers Might Be Ineffective," Journal of Marketing Research 55(1), reports randomized field experiments:

| Setting | Customers offered | Churn without offer | Churn with offer | Leavers won back |
|---|---|---|---|---|
| Wireless provider | 10% most likely to leave (p. 88) | 95.3% | 93.7% | 1.7% |
| Wireless provider | 10% most responsive to the offer (pp. 88–89) | 57.0% | 47.8% | 16.1% |
| Membership organization | 10% most likely to leave (p. 89) | 79.4% | 82.7% | None; the offer increased leaving |

The wireless study measured leaving 30 days after the offer. This assumes the same share of leavers is won back over NorthStar's 90-day window and across a full year.

What is achievable:

| Scenario | Customers kept | Annual churn |
|---|---|---|
| Offer wins back 1.7% (high-risk benchmark) | 2,038 | 17.9% |
| Offer wins back 16.1% (best benchmark) | 19,597 | 17.1% |
| Lever 1: better targeting. The system finds 45% of leavers in the top 10%, the most a top-10% list can hold when 22% of sampled customers leave within 90 days (perfect ranking; same sample-to-year assumption as above). At 16.1% win-back: | 27,455 | 16.7% |
| Lever 2: contacting all 2.1M customers, if the 16.1% win-back held | 61,011 | 15.1% |

A realistic one-year range is 17.1% to 17.9% annual churn, 0.1 to 0.9 points lower than today. This assumes the paper's win-back rates carry over to NorthStar's retail offers, and the 16.1% rate was achieved by targeting the customers most responsive to the offer, not the most at risk. Even contacting every customer would need 22% of leavers won back, above the best result the study reports (16.1%).

For the CFO: each customer kept is worth $340 in lifetime value. 2,038 × $340 = $692,920 a year at the low benchmark, and 19,597 × $340 = $6,662,980 at the best. The 84,000-customer target would be worth $28,560,000. The churn system costs about $131 a month ($1,568 a year) in computing cost only. This excludes review labor and retention offer costs.

### Attribution

| System | Method | Counterfactual | Confidence and reason |
|---|---|---|---|
| Churn Prediction | Randomized holdout. Each week 10% of flagged customers are randomly held back and get no offer. A second randomized test splits customers into two groups: one ranked by the system, one ranked by days since last purchase. Each group's top 10% is contacted, and leaving is compared across the whole of each group, to measure the system's value over the current practice. | The holdout's churn rate is what would have happened without the program. Customers kept = (holdout churn rate − contacted churn rate) × customers contacted, over 90 days | Low today, because no holdout results exist yet. High once a full 90-day window of results is in, because random assignment rules out other explanations |
| Customer Service Agent | Randomized A/B assignment. Routine contacts are randomly split between the agent and human staff | The human-handled group's resolution rate, handle time and cost per contact | Low today, because the agent is not deployed. High once a pilot runs, for the same reason |

### Investment Recommendation

| System | Recommendation | Rationale |
|---|---|---|
| Churn Prediction | Hold | The value is not yet measured at NorthStar. Keep it running, measure what the holdout shows, and reset the 14% target |
| Customer Service Agent | Hold | Costs about $5,950 a month: $3,200 computing and AI service usage plus $2,750 review labor. Its value is unknown because it is not deployed. Run a pilot on a random share of routine contacts, then decide whether to expand |

### Open Questions

| Question | Experiment that closes it |
|---|---|
| Does redeeming an offer actually keep a customer? | For one quarter, randomly send each contacted customer either the offer or a contact with no offer, and compare how many stay after 90 days |
| Does the agent routing a contact correctly lead to a more satisfied customer? | For 4 weeks, randomly give half of routine contacts a stricter rule for passing to staff, then compare audited correctness and satisfaction scores |

## 4. Value Methodology Note

### Value Methodology Note: Churn Prediction

| Field | Value |
|---|---|
| 1. System name | Churn Prediction |
| 2. Business objective | Reduce annual churn by contacting customers most likely to leave with retention offers. The CDO's target is 18% to 14% within a year. Section 3 shows it is not reachable; the realistic range is 17.1% to 17.9%. |
| 3. Value dimension (efficiency / revenue / risk / experience) | Revenue: lifetime value kept |
| 4. Primary metric | Incremental 90-day retention: 90-day retention of contacted customers minus that of the randomized no-offer holdout, per weekly cohort |
| 5. Guardrail metrics (>=2) | (a) Loyalty-tier gap: the difference in the share of churners found between loyalty tiers must stay at or below 10 percentage points (the fairness SLO; a breach freezes deployments).<br>(b) Recall@10%, the share of churners the model places in its top 10%, must stay at or above 0.25 (the prediction-quality SLO). |
| 6. Attribution method | Randomized holdout: 10% of each week's flagged customers get no offer. They are kept out of all retention contact for their full 90 days, even if flagged again (measures the retention program).<br>Second randomized comparison: customers are randomly assigned to a model arm or a rule arm (days since last purchase). Each arm contacts its own top 10%, and churn is compared across each whole arm, not only among contacted customers (measures the model against current practice). |
| 7. Counterfactual | Without the system, the retention team ranks customers by days since last purchase and sends the top 10% each week the same generic 20% discount (the current offer).<br>On the model's own 3,000-customer test split (seed 42), that rule catches 30.0% of churners (198 of 660) against the model's 32.1% (212 of 660), with the same 300 contacts. The handout's rule, over 60 days, flags 19.6% of customers and catches 42.1% of churners, at twice the contact volume.<br>At NorthStar scale (378,000 annual leavers), the rule reaches 113,400 leavers and the model 121,414: about 8,000 more a year (7%).<br>This assumes the sample's 90-day catch rates hold for a full year across 2.1M customers. |
| 8. Measurement window | Weekly cohorts, each read 90 days after it was scored, so the window matches field 9. Holdout customers get no retention contact for their full 90 days. No retention result is reported before day 90; the first read arrives 13 weeks after launch.<br>In between, click-through and offer redemption are reported weekly as early signals only, labeled provisional, and never converted to dollars. |
| 9. Time to observe outcome | 90 days, the churn label's window |
| 10. Confidence level (Low / Medium / High) | Low. No holdout results exist yet.<br>Program effect: High once the first 90-day cohorts are read.<br>Model versus rule: Low to Medium for longer, because the difference is small (about 2 points of churners caught) and needs several 90-day cycles of pooled cohorts. |
| 11. Known confounders | Seasonal promotions and catalog changes, and external shocks such as a competitor's sale or a downturn. Randomization balances them across arms, but they can change the size of the effect, so results are reported by season and shock-period cohorts are flagged. |
| 12. Financial conversion logic | Annual value = customers kept × $340 lifetime value − offer discount cost − churn system cost.<br>Customers kept = (holdout churn rate − contacted churn rate) × customers contacted, counting each customer once, at their first contact in the year (the single group of 210,000 in Section 3).<br>Offer discount cost = redeemed offers × 20% × average order value ($87 in store, $112 online).<br>Churn system cost = $130.69 a month computing plus about $2,406 a month churn review labor (weekly churn review $695.20, retrain and gate review $1,043.20, fairness review $348, holdout analysis $320; Section 2), over 12 months.<br>The model's own value over current practice = extra leavers reached (about 8,000 a year) × the measured win-back rate × $340. |
| 13. Metric owner and review cadence | VP of Marketing (David Park) owns the metric and reviews each cohort weekly. The CDO (Maya Chen) and CFO (Robert Hess) review quarterly checkpoints. |

## 5. Measurement Reflection

### Weak assumption 1: Offer redemption rate -> Incremental 90-day retention

Redemption is a choice, and may come from customers who would have stayed anyway (see the field study in Section 3). The claim to test: offered customers have higher 90-day retention than contacted customers with no offer.

| Item | Design |
|---|---|
| Metric | 90-day retention |
| Population | The week's top-10% contact list after removing the 10% holdout, randomly split into offer vs. contact without offer |
| Duration | One weekly cohort, read 90 days after contact |
| Success criterion | Offer group's 90-day churn is lower; 95% interval of the difference above zero |
| Power | About 58% of contacted customers churn without an offer (121,414 / 210,000, Section 3), assuming their annual churn rate applies over the 90-day window. Detecting the published high-risk effect, 1.7% of leavers won back (57.8% vs 56.8% churn), at 80% power and 5% two-sided significance needs about 40,700 per arm. One week's list of 210,000 covers it |

### Weak assumption 2: Recall@10% -> At-risk list size

Recall@10% is measured only on the test split and list size only on live scoring; better ranking need not push more customers above 0.5.

| Item | Design |
|---|---|
| Metric | At-risk list size and Recall@10%, on the same customers |
| Population | Week's 2.1M scored customers, randomly halved: current model vs. prior approved registry version |
| Duration | 12 weekly cohorts; Recall@10% read at day 90 |
| Success criterion | Where one version's Recall@10% is higher (95% interval excluding zero), its list size is also higher |
| Power | Detecting 0.32 vs 0.34 needs about 8,700 churners per arm. Assuming live 90-day churn is near the sample's 22%, that is about 39,500 customers per arm. Each half (1.05M) far exceeds it |

### Least observed layer: User Experience

For churn, campaign click-through and offer redemption are never joined to scored customers. For the customer service agent, CSAT and seven-day repeat contacts are not collected.

To capture them, ingest campaign events from the marketing platform and redemptions from the loyalty platform. Then add a weekly Glue job that joins them to the scored list by customer and week, and log a post-contact CSAT survey and a seven-day repeat-contact flag by customer ID. This is about 2 weeks of engineering. The first 90-day reading arrives 90 days after data starts flowing.
