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
| Escalation rate | Routine contacts (order status, returns, loyalty points) the agent hands to a human, divided by routine contacts it receives | VP of Customer Experience (Priya Nair) | Weekly | If more than 50% of routine contacts are escalated in a week, review the routing prompt and tool coverage | 0%, 0 of 4 routine scripted scenarios |
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
