#!/usr/bin/env bash
# Churn batch scoring, step 2: make sure a SageMaker Model exists for the
# incumbent (the version in Production) and the candidate (the newest Approved
# version newer than it; see churn-batch-common.sh). Each Model points at its
# Model Registry package, so the container image and model file come from the
# governed record. Existing Models are reused.
#
# Usage: bash scripts/churn-batch-create-models.sh
source "$(dirname "$0")/churn-batch-common.sh"

INC="$(incumbent_version)"
CAND="$(candidate_version)"
[ "$INC" != "None" ]  || fail "no Approved version in stage Production in ${PACKAGE_GROUP}"
[ "$CAND" != "None" ] || fail "no Approved version newer than v${INC} to evaluate in ${PACKAGE_GROUP}"

ROLE_ARN="$(aws iam get-role --role-name "$EXEC_ROLE_NAME" --query Role.Arn --output text)"

ensure_model () {
  local role="$1" version="$2" name
  name="$(model_name "$version")"
  if aws sagemaker describe-model --model-name "$name" >/dev/null 2>&1; then
    echo "${role}: ${name} exists (package ${version})"
    return
  fi
  aws sagemaker create-model --model-name "$name" --execution-role-arn "$ROLE_ARN" \
    --primary-container "ModelPackageName=$(package_arn "$version")" \
    --tags Key=project,Value=northstar Key=lab,Value=lab5 >/dev/null
  echo "${role}: ${name} created (package ${version})"
}

ensure_model incumbent "$INC"
ensure_model candidate "$CAND"
