#!/usr/bin/env bash
# Churn batch scoring: rollback action.
#
# Batch scoring has no traffic weights. Rolling back means the candidate's
# scores are never used: its life-cycle stage becomes Staging/RolledBack and
# its approval status becomes Rejected, so no later run selects it and it can
# never be promoted. The incumbent (the version in Production) is untouched.
#
# Usage: bash deployment/configs/rollback-action.sh <candidate-version>
# shellcheck source=scripts/churn-batch-common.sh
source "$(dirname "$0")/../../scripts/churn-batch-common.sh"

VERSION="${1:?usage: bash deployment/configs/rollback-action.sh <candidate-version>}"
PKG="$(package_arn "$VERSION")"

show () {
  aws sagemaker describe-model-package --model-package-name "$PKG" \
    --query '[ModelApprovalStatus, ModelLifeCycle.Stage, ModelLifeCycle.StageStatus]' --output text
}

# Never roll back the version in Production: that would leave no model in
# use, and every later run would fail to find an incumbent.
STAGE="$(aws sagemaker describe-model-package --model-package-name "$PKG" \
           --query ModelLifeCycle.Stage --output text)"
if [ "$STAGE" = "Production" ]; then
  fail "v${VERSION} is in Production; rolling it back would leave no model in use. To undo a promotion, first promote the version it replaced (bash scripts/churn-batch-promote.sh <previous-version>), then roll v${VERSION} back."
fi

echo "before: ${PACKAGE_GROUP}/${VERSION}  $(show)"
set_stage "$VERSION" Staging RolledBack "Rolled back by deployment/configs/rollback-action.sh"
aws sagemaker update-model-package --model-package-arn "$PKG" \
  --model-approval-status Rejected >/dev/null
echo "after:  ${PACKAGE_GROUP}/${VERSION}  $(show)"
echo "incumbent (Production) remains: v$(incumbent_version)"
