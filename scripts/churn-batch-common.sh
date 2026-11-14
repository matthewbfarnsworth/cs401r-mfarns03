#!/usr/bin/env bash
# Shared settings for the churn batch-scoring scripts (scripts/churn-batch-*).
# Sourced, not run directly.
#
# Every run has a run ID (UTC timestamp, YYYYMMDD-HHMMSS) that names its S3
# folders and its transform jobs, so runs never overwrite each other.
# Every run compares two Model Registry versions, chosen by the registry's
# model life-cycle stage (ModelLifeCycle.Stage / StageStatus):
#   incumbent = the Approved version in stage Production (the model in use)
#   candidate = the newest Approved version that is newer than the incumbent
#               and is not in Production or Archived (the model to evaluate)
# Stages a version moves through:
#   Staging/InEvaluation  set by churn-batch-run-jobs.sh when a run scores it
#   Production/Active     set by churn-batch-promote.sh
#   Archived/Inactive     set by churn-batch-promote.sh on the replaced incumbent
#   Staging/RolledBack    set by deployment/configs/rollback-action.sh (also approval Rejected)
# AWS only builds a SageMaker Model from an Approved version, so a candidate
# is approved before it is evaluated; the stage records whether it is in use.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
REGION="$AWS_DEFAULT_REGION"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="northstar-dev-data-${ACCOUNT}"
PACKAGE_GROUP="northstar-churn-models"
EXEC_ROLE_NAME="northstar-dev-MLEngineer"
PY="${PYTHON:-.venv/bin/python}"

JOB_TEMPLATE="deployment/configs/transform-job.json"
ALARM_CFG="deployment/configs/rollback-alarm.json"
BATCH_ROOT="artifacts/lab5/batch"
JOB_PREFIX="northstar-churn-batch"
REPORT="docs/lab5-batch-comparison.md"

fail () { echo "ERROR: $*" >&2; exit 1; }

new_run_id () { date -u +%Y%m%d-%H%M%S; }

# Newest run ID that has a folder in S3, or empty. Only folders named like a
# run ID (YYYYMMDD-HHMMSS) count.
latest_run_id () {
  aws s3 ls "s3://${BUCKET}/${BATCH_ROOT}/" | awk '/ PRE /{print $2}' | tr -d / \
    | grep -E '^[0-9]{8}-[0-9]{6}$' | sort | tail -1 || true
}

run_input_uri ()  { echo "s3://${BUCKET}/${BATCH_ROOT}/$1/input/"; }
run_output_uri () { echo "s3://${BUCKET}/${BATCH_ROOT}/$1/output/$2/"; }   # run_id role

package_arn () { echo "arn:aws:sagemaker:${REGION}:${ACCOUNT}:model-package/${PACKAGE_GROUP}/$1"; }
model_name ()  { echo "northstar-churn-v$1"; }
job_name ()    { echo "${JOB_PREFIX}-$1-v$2-$3"; }                       # role version run_id

# Approved versions, newest first, one JSON object per line:
#   {"v": 8, "stage": "Staging", "status": "InEvaluation"}
# Stages are read from each version's own record (describe-model-package).
# SageMaker Search can also filter on ModelLifeCycle.Stage, but its index lags
# updates by several seconds (observed 2026-11-14: a new stage took about 8 s
# to appear), so a promote followed by a run could select stale versions.
approved_versions () {
  local v
  for v in $(aws sagemaker list-model-packages --model-package-group-name "$PACKAGE_GROUP" \
               --model-approval-status Approved --sort-by CreationTime --sort-order Descending \
               --query 'ModelPackageSummaryList[].ModelPackageVersion' --output text); do
    aws sagemaker describe-model-package --model-package-name "$(package_arn "$v")" \
      --query '{v: ModelPackageVersion, stage: ModelLifeCycle.Stage, status: ModelLifeCycle.StageStatus}' \
      --output json | jq -c .
  done
}

# The Approved version in Production, or "None". Warns if more than one is.
incumbent_version () {
  local prod
  prod="$(approved_versions | jq -r 'select(.stage == "Production") | .v')"
  if [ "$(printf '%s\n' "$prod" | grep -c .)" -gt 1 ]; then
    echo "WARNING: more than one version is in Production: $(printf '%s ' $prod)- using the newest" >&2
  fi
  prod="$(printf '%s\n' "$prod" | head -1)"
  echo "${prod:-None}"
}

# Newest Approved version newer than the incumbent and not in Production or
# Archived, or "None".
candidate_version () {
  local inc found
  inc="$(incumbent_version)"
  [ "$inc" != "None" ] || { echo "None"; return; }
  found="$(approved_versions | jq -r --argjson inc "$inc" '
    select(.v > $inc and (.stage // "") != "Production" and (.stage // "") != "Archived") | .v' | head -1)"
  echo "${found:-None}"
}

# Set a version's life-cycle stage: set_stage <version> <stage> <status> [description]
set_stage () {
  aws sagemaker update-model-package --model-package-arn "$(package_arn "$1")" \
    --model-life-cycle "$(jq -nc --arg s "$2" --arg st "$3" --arg d "${4:-}" \
      '{Stage: $s, StageStatus: $st} + (if $d != "" then {StageDescription: $d} else {} end)')" >/dev/null
}

# Transform job for a role (incumbent|candidate) in a run, or "None".
run_job () {
  local found
  found="$(aws sagemaker list-transform-jobs --name-contains "${JOB_PREFIX}-$1-" \
             --query "TransformJobSummaries[?ends_with(TransformJobName, '-$2')].TransformJobName" \
             --output text | tr '\t' '\n' | grep -v '^$' | head -1 || true)"
  echo "${found:-None}"
}

# Registry version a transform job scored with (read from its Model name).
job_version () {
  aws sagemaker describe-transform-job --transform-job-name "$1" \
    --query ModelName --output text | sed 's/.*-v//'
}
