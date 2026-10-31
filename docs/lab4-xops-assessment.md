# Lab 4: XOps Assessment

## CI/CD Pipeline (Task 2)

### Why AWS CodePipeline

I used AWS CodePipeline with CodeBuild because the rest of the platform already runs on AWS. Its IAM roles, SageMaker training, Model Registry, CloudWatch alarm and SNS approval topic all work together without storing long-lived AWS keys in GitHub. The GitHub connection is authorized once, and `pipeline.yaml` keeps the stack in code. The tradeoff is more AWS setup than GitHub Actions would need.

### Stage mapping

The pipeline has three CodePipeline stages that perform the five required phases.

| Required phase | CodePipeline stage | Where it happens | On failure |
|---|---|---|---|
| Source | `Source` | CodeConnections pulls a push to `main` | The run does not start |
| Test | `Build` | `buildspec.yml` `pre_build`: `tests/test_data.py`, `tests/test_features.py` | Stops in `PRE_BUILD`; nothing trains |
| Build | `Build` | `buildspec.yml` `build`: upserts and runs the SageMaker Pipeline, which trains through `churn_model.train()` on spot capacity | Stops in `BUILD` |
| Evaluate | `Build` | `evaluate_model()` inside the training job, then `tests/test_model.py` and `tests/test_fairness.py` in `build` | See below |
| Register | `Build` | SageMaker Pipeline `RegisterModel` step, as `PendingManualApproval` | See below |
| Human promotion decision | `ManualApproval` | SNS-notified approval action | The model stays unapproved |

### Where Evaluate and Register overlap

Evaluation happens at two points, on either side of registration:

1. **Before registration:** `evaluate_model()` raises if precision@10%, recall@10% or the lift CI fails. The training step then fails, so nothing registers.
2. **After registration:** the CodeBuild gate runs `tests/test_model.py`, including the champion comparison, and `tests/test_fairness.py`. If it fails, `post_build` sets the package to `Rejected`, so a red build leaves nothing approvable. Package 2 is a real example of this.

Approving in CodePipeline does not change the Model Registry. Approval there and `update-model-package` are two separate steps.

Evidence: `docs/lab4-pipeline-runs.txt`, `docs/lab4-failure-demo.txt`, `docs/lab4-build-green.log`.

## Major Decisions

### Changes from the lab's defaults

- **Recency constraint added to the Lab 3 model.** `HYPERPARAMS` in `models/churn/churn_model.py` adds `monotone_constraints` on `days_since_last_purchase`. Every other hyperparameter is the Lab 3 tuning winner.
  - *Why:* build 2 failed `test_model_monotone_on_recency` by 0.001: the churn score fell 0.021 between 60 and 90 days, and the test allows 0.02. A retrain with the same data and seed would not be expected to fix it, and 7 local setups across XGBoost 1.7.6 and 3.2.0 all dipped by 0.038 to 0.119.
  - *Rejected alternative:* raising the tolerance would weaken a provided test just to get a pass.
  - *Effect:* this departs from "reproduce Lab 3 hyperparameters", so the pipeline model is not the exact Lab 3 model. Lab 3 reported AUC 0.7785, precision 0.7067, recall 0.3212. The pipeline model reports AUC 0.7838, precision 0.7267, recall 0.3303. Part of that difference may come from the container's XGBoost 1.7, which the lab says to expect.
- **Trained on `ml.g6.xlarge` spot instead of `ml.m5.large` spot.** My account's `ml.m5.large` spot quota (`L-29688C85`) is 0 rather than the documented 4, and my increase request was still open. `ml.g5.xlarge` timed out in build 1 waiting for capacity. `buildspec.yml` overrides `TrainingInstanceType`, and spot, `max_run` and `max_wait` are unchanged. The training code does not use the GPU, so this is a capacity workaround, not a performance choice. It billed 74 seconds.

### Fairness

- **Package 3 was approved as a demonstration only.** The fairness flag fired on every green run: recall was 44.0% for Bronze and 14.3% for Platinum, a 29.8 point gap against the 10 point threshold. I would not approve this model for production use. I approved it because the champion comparison needs an Approved package, and I recorded the objection in its approval description. It will be set to `Rejected` at teardown.
- **The flag compares Platinum with Bronze.** I read "highest and lowest loyalty tier" as tier rank, not best and worst recall. The flag warns but does not block, as the lab requires.

### Tests

- **Freshness checks the oldest row.** The lab says latest `event_time`. No row may be stale, so I check the oldest, which is stricter.
- **The champion lookup fails loudly.** If the registry or metrics file cannot be read, the test fails. It only skips when no package is Approved, so a pipeline problem cannot look like "no champion".

### Training and features

- **Hyperparameters are hard-coded.** Each run is reproducible, and changing a setting takes a commit that passes the same gates.
- **`churn_model.py` and the Lab 3 script stay separate copies.** This is simple, but they can drift apart.
- **Per-tier results are stored as `metrics["slices"]`**, the list `evaluate_slices` already returns.
- **Customers with no purchases get recency 999 and 0 for everything else.** Glue only produces customers with purchases, so the model never sees these values, but the required boundary test needs defined behavior.
- **`compute_behavioral_features` takes an optional `snapshot_date`.** It filters out post-cutoff purchases when given a date, which reduces the risk of leaking holdout data.

## MLOps Configuration (Task 3)

### Champion-challenger criterion

A challenger replaces the champion only if all of these are true:

1. It passes every CI gate.
2. Its AUC is at least the champion's AUC.
3. Its Platinum-to-Bronze recall gap is no wider than the champion's gap.

Both models are compared on the same recent customers. Ties go to the challenger because it trained on newer data. No regression is allowed, because small allowed drops could add up over time. Today's bar is AUC 0.7838 and a 29.8 point gap.

This is stricter than the CI test, which allows AUC down to champion - 0.02. The CI test blocks bad models; this rule decides promotion at the human approval step.

Limitation: AUC varies by chance. Across 50 train/test splits of my data, only 34% reached 0.7838, so comparing scores from different splits would let a lucky champion block equal models. The same-customers comparison addresses this, but it is not automated yet.

### Retraining triggers

**Scheduled:** every week. Amazon EventBridge Scheduler starts the Glue feature job, and an EventBridge rule on that job's success starts the pipeline with `StartPipelineExecution`. Training costs about $0.02 per run, so keeping the model current is worth more than the cost. Features must run first because a feature set older than 48 hours fails the build.

**Performance:** when AUC falls below 0.76 on the latest customers whose 90-day outcome is known. SageMaker Model Monitor (model quality) publishes the metric, a CloudWatch alarm watches it, and an EventBridge rule on the alarm starts the same Glue-then-pipeline path. Model Monitor needs a deployed endpoint with data capture and ground truth labels, which I do not have until a model is served.

I chose 0.76 from measured noise. Across 50 splits, AUC averaged 0.7795 and never fell below 0.7608, so a score under 0.76 is unlikely to be chance. A check needs about 3,000 customers to match that measurement. The threshold should be recalculated if the data changes much, since it reflects chance variation and not observed production decay.

Limitations: outcomes take 90 days to become known, so this trigger catches decay late. `FEATURE_CUTOFF` and `SNAPSHOT` are also hard-coded in `glue-scripts/feature_engineer.py`, so scheduled retraining needs rolling dates before it can learn from new labels.

### Experiment tracking

`docs/lab4-mlflow-runs.txt` shows `mlflow.search_runs` output from my `northstar-mlflow` MLflow App: 10 tuning runs with different hyperparameters and the final Lab 3 run. These runs predate the recency constraint added in Lab 4.

### Model lineage

Packages 3, 4 and 5 store the commit SHA, training data URI, data timestamp and evaluation metrics location as customer metadata, shown in `docs/lab4-model-package.json`. I added these from my shell, so future packages will not get them automatically. Adding this to `buildspec.yml` is a known gap.

## DataOps Maturity (Task 4)

*To do.*

## MLOps Maturity (Task 4)

*To do.*
