# NorthStar Churn Model: Weekly Batch Scoring Deployment Plan

## 1. Deployment strategy and rationale

NorthStar scores every active customer (about 2.1M) weekly; scores are due Monday 6 AM ET for the retention campaign targeting the top 10% highest-risk customers. Deploy as a **batch parallel (shadow) run**: the candidate (newest Approved version in `northstar-churn-models` newer than Production) scores the same input as the incumbent (the Production version), and outputs are compared before candidate scores feed anything downstream. Canary and blue/green do not fit: there is no live traffic to split, and an always-on endpoint bills all week for a weekly job, while two transform jobs bill minutes.

## 2. Pre-deployment checklist

Run commands from the repository root. `<version>` is the candidate's version number (listed by the approval item's first command).

- [ ] **Setup.** Sign in with your own AWS credentials (AWS CLI v2):
  ```bash
  export AWS_DEFAULT_REGION=us-east-1
  ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
  BUCKET=northstar-dev-data-$ACCOUNT
  PKG=arn:aws:sagemaker:us-east-1:$ACCOUNT:model-package/northstar-churn-models
  jq --version
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
  ```
- [ ] **Features refreshed within 48 hours** (newest dates):
  ```bash
  aws s3 ls --recursive s3://$BUCKET/features/customers/ | sort | tail -3
  ```
- [ ] **Transform quota ≥ 2** (both jobs run at once on ml.m5.large):
  ```bash
  aws service-quotas get-service-quota --service-code sagemaker --quota-code L-236AE59F --query Quota.Value
  ```
- [ ] **Model approved by the lead ML engineer**, who must not be the engineer who trained or pushed it:
  ```bash
  aws sagemaker list-model-packages --model-package-group-name northstar-churn-models --query 'ModelPackageSummaryList[].[ModelPackageVersion,ModelApprovalStatus]' --output table
  aws s3 cp $(aws sagemaker describe-model-package --model-package-name $PKG/<version> --query ModelMetrics.ModelQuality.Statistics.S3Uri --output text) - | jq .
  ```
  Approve only if `auc_roc` > `baseline_auc`, `lift_ci_low` > 0 (lift CI excludes zero), `precision_top10` ≥ 0.50, `recall_top10` ≥ 0.25, and each loyalty tier in `slices` is reviewed for `recall_drop_over_10pp`:
  ```bash
  aws sagemaker update-model-package --model-package-arn $PKG/<version> --model-approval-status Approved
  ```
- [ ] **Candidate is Approved and newer than Production** (prints both versions, creates their Models; fails if none):
  ```bash
  bash scripts/churn-batch-create-models.sh
  ```
- [ ] **Candidate's model.tar.gz contains only `xgboost-model`** (the container loads every file as a model):
  ```bash
  aws s3 cp $(aws sagemaker describe-model-package --model-package-name $PKG/<version> --query 'InferenceSpecification.Containers[0].ModelDataUrl' --output text) - | tar -tzf -
  ```
- [ ] **Rollback alarm exists:**
  ```bash
  bash scripts/churn-batch-create-rollback-alarm.sh
  ```

## 3. Rollback criteria

From `deployment/configs/rollback-alarm.json`: alarm `northstar-churn-rollback-disagreement` fires when `PredictionDisagreementPct` (namespace `NorthStar/ChurnBatch`, dimension `Pipeline=northstar-churn-batch`, `Maximum`, 300 s period, 1 datapoint) is `GreaterThanThreshold` **5** (unit `Percent`). The metric is the percentage of customers whose churn label (score ≥ 0.5) differs between incumbent and candidate; 5 means 5%, not 0.05.

**Who makes the call:** the rule decides; the lead ML engineer executes it without discretion. If the alarm is `ALARM` (equivalently, `churn-batch-compare.sh` prints `ROLLBACK TRIGGERED` and exits 1):
```bash
bash deployment/configs/rollback-action.sh <version>
```
A failed or incomplete run (a job not `Completed`, or customers scored by only one model) is not a rollback: no promotion; the candidate stays in Staging and the run is repeated.

## 4. Monitoring window

The window is **48 hours**, from the shadow run's start (Saturday 6 AM ET) to the Monday 6 AM ET deadline. Start it:
```bash
bash scripts/churn-batch-prepare-input.sh   # last line prints "run id: <run-id>"
RUN_ID=<run-id>
bash scripts/churn-batch-run-jobs.sh $RUN_ID
bash scripts/churn-batch-compare.sh $RUN_ID
aws cloudwatch describe-alarms --alarm-names northstar-churn-rollback-disagreement --query 'MetricAlarms[].StateValue' --output text
```

**Watch:** disagreement %; incumbent versus candidate predicted-churn share in `docs/lab5-batch-comparison.md`; both jobs `Completed`, every customer matched; feature freshness. After promotion, also track offer redemption and feature drift.

**Clean deployment:** both jobs `Completed`, zero unmatched customers, disagreement ≤ 5%, alarm `OK` at window end. Then promote:
```bash
bash scripts/churn-batch-promote.sh <version>
```

48 hours catch what a short window cannot: a fresh nightly POS load, weekend-versus-weekday shifts, time for the campaign manager to sanity-check the top-10% list, and retries during hours-long AWS capacity shortages. The lab compressed 48 h to about 60 minutes (one shadow run), trading these away.

## 5. Stakeholder notifications

Emailed by the operator.

| Role | Deployment start | Promotion | Rollback |
|---|---|---|---|
| ML engineering (operator and lead ML engineer) | Run id, incumbent and candidate versions | Promotion done | Rollback done, with disagreement % |
| VP of Marketing (business owner) | Heads-up: candidate in evaluation | New model will score Monday's list | Current model stays; campaign unchanged |
| Retention campaign manager (uses the list) | No action yet | Monday's list uses the new model | Monday's list uses the current model |

## 6. Post-deployment review

**When:** Wednesday after the first Monday campaign using the promoted model. **Who:** the three roles in section 5. **Artifact:** `docs/reviews/<run-id>-review.md` (the promoting run), plus the version's `StageDescription` (the call requires Stage and StageStatus; keep the existing ones):
```bash
aws sagemaker update-model-package --model-package-arn $PKG/<version> --model-life-cycle '{"Stage":"Production","StageStatus":"Active","StageDescription":"Reviewed: see docs/reviews/<run-id>-review.md"}'
```

## 7. Resource cleanup

Keep the rollback alarm and the Production version's Model; finished transform jobs no longer bill. In order:

1. Confirm no transform job is running:
   ```bash
   aws sagemaker list-transform-jobs --status-equals InProgress --query 'TransformJobSummaries[].TransformJobName'
   ```
2. Delete the Model of the rolled-back candidate or the archived incumbent (printed by `churn-batch-promote.sh`):
   ```bash
   aws sagemaker delete-model --model-name northstar-churn-v<version>
   ```
3. Delete each run's folder (run ids listed below) after 30 days; scores are personal data:
   ```bash
   aws s3 rm --recursive s3://$BUCKET/artifacts/lab5/batch/<run-id>/
   ```

**Verify:** step 1 returns `[]`; `aws sagemaker list-models --name-contains northstar-churn-v --query 'Models[].ModelName'` shows only needed Models; `aws s3 ls s3://$BUCKET/artifacts/lab5/batch/` lists no run id (a UTC date) older than 30 days.
