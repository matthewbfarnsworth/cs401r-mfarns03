#!/usr/bin/env bash
# Churn batch scoring, step 3: score a run's input with the incumbent and the
# candidate at the same time (the parallel run). Both jobs are built from
# deployment/configs/transform-job.json; the model, job name, S3 folders and
# tags are filled in here. Marks the candidate Staging/InEvaluation in the
# Model Registry. Waits for both jobs, then reports. Exits non-zero unless both
# jobs Completed.
#
# Usage: bash scripts/churn-batch-run-jobs.sh [run-id]
#        run-id defaults to the newest run in S3.
source "$(dirname "$0")/churn-batch-common.sh"

RUN_ID="${1:-$(latest_run_id)}"
[ -n "$RUN_ID" ] || fail "no run found; start one with scripts/churn-batch-prepare-input.sh"
aws s3 ls "$(run_input_uri "$RUN_ID")" >/dev/null || fail "run ${RUN_ID} has no input in $(run_input_uri "$RUN_ID")"

INC="$(incumbent_version)"
CAND="$(candidate_version)"
[ "$INC" != "None" ]  || fail "no Approved version in stage Production"
[ "$CAND" != "None" ] || fail "no Approved version newer than v${INC} to evaluate"
for v in "$INC" "$CAND"; do
  aws sagemaker describe-model --model-name "$(model_name "$v")" >/dev/null 2>&1 \
    || fail "Model $(model_name "$v") missing; run scripts/churn-batch-create-models.sh"
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

start_job () {
  local role="$1" version="$2" name
  name="$(job_name "$role" "$version" "$RUN_ID")"
  jq --arg name "$name" --arg model "$(model_name "$version")" \
     --arg in "$(run_input_uri "$RUN_ID")" --arg out "$(run_output_uri "$RUN_ID" "$role")" \
     --arg role "$role" --arg version "$version" --arg run "$RUN_ID" '
       .TransformJobName = $name
     | .ModelName = $model
     | .TransformInput.DataSource.S3DataSource.S3Uri = $in
     | .TransformOutput.S3OutputPath = $out
     | .Tags += [ {Key: "deployment-role", Value: $role},
                  {Key: "model-package-version", Value: $version},
                  {Key: "run-id", Value: $run} ]
     ' "$JOB_TEMPLATE" > "$WORK/${role}.json"
  aws sagemaker create-transform-job --cli-input-json "file://${WORK}/${role}.json" >/dev/null
  echo "$name"
}

set_stage "$CAND" Staging InEvaluation "Scored against v${INC} in batch run ${RUN_ID}"

JI="$(start_job incumbent "$INC")"
JC="$(start_job candidate "$CAND")"
echo "run ${RUN_ID}: incumbent v${INC} (Production), candidate v${CAND} (Staging/InEvaluation)"
echo "started incumbent: ${JI}"
echo "started candidate: ${JC}"

running () { [ "$1" = "InProgress" ] || [ "$1" = "Stopping" ]; }
while true; do
  si="$(aws sagemaker describe-transform-job --transform-job-name "$JI" --query TransformJobStatus --output text)"
  sc="$(aws sagemaker describe-transform-job --transform-job-name "$JC" --query TransformJobStatus --output text)"
  echo "[$(date -u +%H:%M:%S)] incumbent=${si}  candidate=${sc}"
  if ! running "$si" && ! running "$sc"; then break; fi
  sleep 30
done

ok=0
for j in "$JI" "$JC"; do
  aws sagemaker describe-transform-job --transform-job-name "$j" \
    --query '[TransformJobName,TransformJobStatus,FailureReason]' --output text
  [ "$(aws sagemaker describe-transform-job --transform-job-name "$j" \
         --query TransformJobStatus --output text)" = "Completed" ] || ok=1
done
exit "$ok"
