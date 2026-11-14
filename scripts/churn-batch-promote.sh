#!/usr/bin/env bash
# Churn batch scoring: promotion action.
#
# Promoting means the candidate becomes the model in use: its life-cycle stage
# becomes Production/Active, and the version it replaces becomes
# Archived/Inactive. The candidate is set to Production first, so there is
# never a moment with no version in Production.
#
# Usage: bash scripts/churn-batch-promote.sh <candidate-version>
source "$(dirname "$0")/churn-batch-common.sh"

VERSION="${1:?usage: bash scripts/churn-batch-promote.sh <candidate-version>}"
PKG="$(package_arn "$VERSION")"

[ "$(aws sagemaker describe-model-package --model-package-name "$PKG" \
       --query ModelApprovalStatus --output text)" = "Approved" ] \
  || fail "${PACKAGE_GROUP}/${VERSION} is not Approved; only an Approved version can go to Production"

OLD="$(incumbent_version)"
[ "$OLD" != "$VERSION" ] || fail "v${VERSION} is already in Production"

set_stage "$VERSION" Production Active "Promoted by scripts/churn-batch-promote.sh, replacing v${OLD}"
if [ "$OLD" != "None" ]; then
  set_stage "$OLD" Archived Inactive "Replaced in Production by v${VERSION}"
fi

for v in "$VERSION" "$OLD"; do
  [ "$v" != "None" ] || continue
  echo "${PACKAGE_GROUP}/${v}  $(aws sagemaker describe-model-package --model-package-name "$(package_arn "$v")" \
    --query '[ModelApprovalStatus, ModelLifeCycle.Stage, ModelLifeCycle.StageStatus]' --output text)"
done
