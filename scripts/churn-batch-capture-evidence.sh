#!/usr/bin/env bash
# Churn batch scoring: write CLI evidence to docs/lab5-deployment-output.txt,
# in the three windows and section numbers the Lab 5 rubric uses.
#
#   live      window 1 (overwrites the file): sections 0-4, after
#             scripts/churn-batch-compare.sh and before any rollback.
#               0 Model Registry   1 both transform jobs   2 transform quota
#               3 rollback alarm   4 comparison result
#   rollback  window 2 (appends): section 5, after
#             deployment/configs/rollback-action.sh.
#   teardown  window 3 (appends): section 6, after scripts/teardown-lab5.sh.
#
# Errors are written into the file instead of stopping the script, so an
# AccessDenied shows up where the table should be. Read the file afterwards.
#
# Usage: bash scripts/churn-batch-capture-evidence.sh [live|rollback|teardown] [run-id]
#        run-id defaults to the newest run in S3.
source "$(dirname "$0")/churn-batch-common.sh"
set +e

OUT="docs/lab5-deployment-output.txt"
MODE="${1:-live}"
RUN_ID="${2:-$(latest_run_id)}"
cap () { echo; echo "===== $1  ($(date -u +%Y-%m-%dT%H:%M:%SZ)) ====="; }
since () { date -u -v-6H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '6 hours ago' +%Y-%m-%dT%H:%M:%SZ; }

registry_table () {
  printf '%-8s %-22s %-11s %-13s %s\n' Version ApprovalStatus Stage StageStatus CreationTime
  for v in $(aws sagemaker list-model-packages --model-package-group-name "$PACKAGE_GROUP" \
               --query 'ModelPackageSummaryList[].ModelPackageVersion' --output text); do
    aws sagemaker describe-model-package --model-package-name "$(package_arn "$v")" \
      --query '[ModelPackageVersion, ModelApprovalStatus, ModelLifeCycle.Stage, ModelLifeCycle.StageStatus, CreationTime]' \
      --output text | awk '{printf "%-8s %-22s %-11s %-13s %s\n", $1, $2, $3, $4, $5}'
  done
  local cand
  cand="$(candidate_version)"
  [ "$cand" = "None" ] || cand="v${cand}"
  echo "incumbent (Production): v$(incumbent_version)   candidate: ${cand}"
}

window1 () {
  local ji jc
  ji="$(run_job incumbent "$RUN_ID")"
  jc="$(run_job candidate "$RUN_ID")"
  echo "Lab 5 deployment evidence (batch transform path) - account ${ACCOUNT} - run ${RUN_ID}"

  cap "0. Model Registry: versions, approval status and life-cycle stage"
  registry_table

  cap "1. describe-transform-job: incumbent (package $(job_version "$ji")) and candidate (package $(job_version "$jc"))"
  aws sagemaker describe-transform-job --transform-job-name "$ji"
  aws sagemaker describe-transform-job --transform-job-name "$jc"

  cap "2. Transform quota: ml.m5.large for transform job usage (L-236AE59F)"
  aws service-quotas list-requested-service-quota-change-history --service-code sagemaker --region us-east-1 \
    --query "RequestedQuotas[?QuotaCode=='L-236AE59F'].[QuotaName,DesiredValue,Status,Created,LastUpdated]" --output table
  echo "applied quota value: $(aws service-quotas get-service-quota --service-code sagemaker \
    --quota-code L-236AE59F --region us-east-1 --query 'Quota.Value' --output text)"

  cap "3. rollback alarm: definition as code, then live state"
  cat "$ALARM_CFG"
  aws cloudwatch describe-alarms --alarm-names "$(jq -r .AlarmName "$ALARM_CFG")" \
    --query 'MetricAlarms[].[AlarmName,MetricName,Statistic,Threshold,Unit,EvaluationPeriods,StateValue,StateUpdatedTimestamp]' --output table
  aws cloudwatch describe-alarms --alarm-names "$(jq -r .AlarmName "$ALARM_CFG")" \
    --query 'MetricAlarms[].StateReason' --output text

  cap "4. observed comparison: published disagreement metric and $(basename "$REPORT")"
  aws cloudwatch get-metric-statistics \
    --namespace "$(jq -r .Namespace "$ALARM_CFG")" --metric-name "$(jq -r .MetricName "$ALARM_CFG")" \
    --dimensions "$(jq -r '.Dimensions[0] | "Name=\(.Name),Value=\(.Value)"' "$ALARM_CFG")" \
    --start-time "$(since)" --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --period 300 --statistics Maximum --unit Percent \
    --query 'sort_by(Datapoints,&Timestamp)[].[Timestamp,Maximum,Unit]' --output table
  sed -n '/^## Disagreement/,$p' "$REPORT"
}

window2 () {
  cap "5. after rollback: Model Registry"
  registry_table
}

window3 () {
  cap "6. after teardown: live API"
  echo "endpoints:              $(aws sagemaker list-endpoints --query 'length(Endpoints)' --output text)"
  echo "endpoint configs:       $(aws sagemaker list-endpoint-configs --query 'length(EndpointConfigs)' --output text)"
  echo "scalable targets:       $(aws application-autoscaling describe-scalable-targets --service-namespace sagemaker --query 'length(ScalableTargets)' --output text)"
  echo "SageMaker models:       $(aws sagemaker list-models --query 'length(Models)' --output text)"
  echo "transform jobs running: $(aws sagemaker list-transform-jobs --status-equals InProgress --query 'length(TransformJobSummaries)' --output text)"
}

case "$MODE" in
  live)     window1 2>&1 | tee "$OUT" ;;
  rollback) window2 2>&1 | tee -a "$OUT" ;;
  teardown) window3 2>&1 | tee -a "$OUT" ;;
  *)        echo "usage: bash scripts/churn-batch-capture-evidence.sh [live|rollback|teardown] [run-id]" >&2; exit 2 ;;
esac
