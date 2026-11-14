#!/usr/bin/env bash
# Churn batch scoring, step 4: compare a run's two outputs, publish the
# disagreement rate to CloudWatch (which the rollback alarm watches), and
# write docs/lab5-batch-comparison.md.
# Exits 1 if the disagreement rate breaches the threshold in rollback-alarm.json.
#
# Usage: bash scripts/churn-batch-compare.sh [run-id]
#        run-id defaults to the newest run in S3.
source "$(dirname "$0")/churn-batch-common.sh"

RUN_ID="${1:-$(latest_run_id)}"
[ -n "$RUN_ID" ] || fail "no run found"

JI="$(run_job incumbent "$RUN_ID")"
JC="$(run_job candidate "$RUN_ID")"
for j in "$JI" "$JC"; do
  [ "$j" != "None" ] || fail "run ${RUN_ID} is missing a transform job; run scripts/churn-batch-run-jobs.sh ${RUN_ID}"
  s="$(aws sagemaker describe-transform-job --transform-job-name "$j" --query TransformJobStatus --output text)"
  [ "$s" = "Completed" ] || fail "${j} is ${s}, not Completed"
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
for role in incumbent candidate; do
  aws s3 cp "$(run_output_uri "$RUN_ID" "$role")" "$WORK/${role}/" \
    --recursive --quiet --exclude "*" --include "*.out"
done

"$PY" scripts/churn_batch_compare.py "$WORK" "$RUN_ID" \
  "$JI" "$(job_version "$JI")" "$JC" "$(job_version "$JC")"

VALUE="$(cat "$WORK/disagreement_pct")"
# put-metric-data takes dimensions as Key=Value pairs ("Pipeline=northstar-churn-batch"),
# NOT the Name=...,Value=... form other CloudWatch commands use. The Name/Value
# form is accepted without error but stores two wrong dimensions ("Name" and
# "Value"), so the alarm never sees the data point (observed 2026-11-14).
DIMS="$(jq -r '.Dimensions | map("\(.Name)=\(.Value)") | join(",")' "$ALARM_CFG")"
aws cloudwatch put-metric-data \
  --namespace "$(jq -r .Namespace "$ALARM_CFG")" \
  --metric-name "$(jq -r .MetricName "$ALARM_CFG")" \
  --dimensions "$DIMS" \
  --unit "$(jq -r .Unit "$ALARM_CFG")" \
  --value "$VALUE"
echo "published $(jq -r .MetricName "$ALARM_CFG") = ${VALUE}"

if [ "$(cat "$WORK/verdict")" = "BREACHED" ]; then
  echo "ROLLBACK TRIGGERED: disagreement ${VALUE}% exceeds the threshold. Run: bash deployment/configs/rollback-action.sh $(job_version "$JC")"
  exit 1
fi
echo "Within threshold: the candidate may be promoted with scripts/churn-batch-promote.sh $(job_version "$JC")"
