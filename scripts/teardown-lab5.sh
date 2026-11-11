#!/usr/bin/env bash
# teardown-lab5.sh
# Deletes everything Lab 5 can leave running and bills for.
#
# Why this exists when teardown-lab3.sh already deletes endpoints:
#   * it writes docs/lab5-teardown-output.txt, which is the Lab 5 evidence gate
#   * it deletes SageMaker Models and the rollback CloudWatch alarms, neither of
#     which the Lab 3 script touches
#   * it asserts zero auto-scaling scalable targets in the verification block
#
# VERIFIED 2026-07-30: Application Auto Scaling cleans up after itself. Deleting
# the endpoint automatically deregisters the scalable target, deletes its
# scaling policies, and deletes the TargetTracking-* alarms it created. There is
# no orphan class here. Steps 1 and 2 below are therefore defensive - they make
# the teardown deterministic and keep the verification honest when a student
# deletes resources out of order or by hand in the console.
#
# Usage: bash scripts/teardown-lab5.sh
set -uo pipefail
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
mkdir -p docs

echo "==> Lab 5 teardown  (account $(aws sts get-caller-identity --query Account --output text))"

echo "[1/5] Deleting auto-scaling policies and deregistering scalable targets"
aws application-autoscaling describe-scalable-targets \
  --service-namespace sagemaker \
  --query 'ScalableTargets[*].ResourceId' --output text 2>/dev/null | tr '\t' '\n' | while read -r rid; do
  [ -z "$rid" ] && continue
  aws application-autoscaling describe-scaling-policies \
    --service-namespace sagemaker --resource-id "$rid" \
    --query 'ScalingPolicies[*].PolicyName' --output text 2>/dev/null | tr '\t' '\n' | while read -r pol; do
    [ -z "$pol" ] && continue
    aws application-autoscaling delete-scaling-policy \
      --service-namespace sagemaker --resource-id "$rid" \
      --scalable-dimension sagemaker:variant:DesiredInstanceCount \
      --policy-name "$pol" >/dev/null 2>&1 && echo "      deleted scaling policy $pol"
  done
  aws application-autoscaling deregister-scalable-target \
    --service-namespace sagemaker --resource-id "$rid" \
    --scalable-dimension sagemaker:variant:DesiredInstanceCount >/dev/null 2>&1 \
    && echo "      deregistered scalable target $rid"
done

echo "[2/5] Deleting CloudWatch alarms for the rollback trigger"
# The prefix is "northstar-churn-rollback", NOT "northstar". A bare "northstar"
# prefix matched every alarm on the account whose name starts that way, so this
# loop deleted northstar-ci-build-failure -- the Lab 4 CI gate-failure alarm,
# owned by the northstar-cicd CloudFormation stack and added because the rubric
# required it. Running Lab 5 teardown silently disarmed a Lab 4 control and left
# the stack drifted from its template. Observed 2026-08-06.
# TargetTracking-* alarms are not listed here on purpose: deregistering the
# scalable target in [1/5] deletes the ones auto-scaling created.
for a in $(aws cloudwatch describe-alarms --alarm-name-prefix northstar-churn-rollback \
             --query 'MetricAlarms[*].AlarmName' --output text 2>/dev/null | tr '\t' '\n'); do
  [ -z "$a" ] && continue
  aws cloudwatch delete-alarms --alarm-names "$a" >/dev/null 2>&1 && echo "      deleted alarm $a"
done

echo "[3/5] Deleting SageMaker endpoints"
for e in $(aws sagemaker list-endpoints --query 'Endpoints[*].EndpointName' --output text 2>/dev/null | tr '\t' '\n'); do
  [ -z "$e" ] && continue
  aws sagemaker delete-endpoint --endpoint-name "$e" >/dev/null 2>&1 && echo "      deleted endpoint $e"
done

echo "[4/5] Deleting endpoint configs and models"
for c in $(aws sagemaker list-endpoint-configs --query 'EndpointConfigs[*].EndpointConfigName' --output text 2>/dev/null | tr '\t' '\n'); do
  [ -z "$c" ] && continue
  aws sagemaker delete-endpoint-config --endpoint-config-name "$c" >/dev/null 2>&1 && echo "      deleted config $c"
done
for m in $(aws sagemaker list-models --query 'Models[*].ModelName' --output text 2>/dev/null | tr '\t' '\n'); do
  [ -z "$m" ] && continue
  aws sagemaker delete-model --model-name "$m" >/dev/null 2>&1 && echo "      deleted model $m"
done

echo "[5/5] Stopping in-flight transform / processing jobs"
for j in $(aws sagemaker list-transform-jobs --status-equals InProgress --query 'TransformJobSummaries[*].TransformJobName' --output text 2>/dev/null | tr '\t' '\n'); do
  [ -z "$j" ] && continue
  aws sagemaker stop-transform-job --transform-job-name "$j" >/dev/null 2>&1 && echo "      stopped transform job $j"
done
for j in $(aws sagemaker list-processing-jobs --status-equals InProgress --query 'ProcessingJobSummaries[*].ProcessingJobName' --output text 2>/dev/null | tr '\t' '\n'); do
  [ -z "$j" ] && continue
  aws sagemaker stop-processing-job --processing-job-name "$j" >/dev/null 2>&1 && echo "      stopped processing job $j"
done

echo ""
echo "==> Verifying against the live API (not the console, which lags hours)"
{
  echo "Lab 5 teardown verification - $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Account: $(aws sts get-caller-identity --query Account --output text)  Region: $REGION"
  echo ""
  fail=0
  check () {
    # Sum across lines instead of comparing the raw string. The AWS CLI applies
    # --query PER PAGE, so a paginated call returns one length() per page:
    # list-processing-jobs returned "0\n0" on a clean account, which is not the
    # string "0", so every successful teardown reported
    #   Processing jobs running   STILL PRESENT: 0
    # followed by the "resources may still be billing" warning. A false failure
    # on the one script whose whole job is to tell you the account is clean.
    # Observed 2026-08-06; same --query-per-page bug already documented in
    # CLAUDE.md for service-quotas. Summing is correct for both the single-page
    # and multi-page cases.
    n=$(eval "$2" 2>/dev/null | awk '{s+=$1} END {print s+0}'); [ -z "$n" ] && n=0
    if [ "$n" = "0" ]; then printf "  %-32s OK\n" "$1"
    else printf "  %-32s STILL PRESENT: %s\n" "$1" "$n"; fail=1; fi
  }
  check "SageMaker endpoints"      "aws sagemaker list-endpoints --query 'length(Endpoints)' --output text"
  check "Endpoint configs"         "aws sagemaker list-endpoint-configs --query 'length(EndpointConfigs)' --output text"
  check "SageMaker models"         "aws sagemaker list-models --query 'length(Models)' --output text"
  check "Auto-scaling targets"     "aws application-autoscaling describe-scalable-targets --service-namespace sagemaker --query 'length(ScalableTargets)' --output text"
  check "Transform jobs running"   "aws sagemaker list-transform-jobs --status-equals InProgress --query 'length(TransformJobSummaries)' --output text"
  check "Processing jobs running"  "aws sagemaker list-processing-jobs --status-equals InProgress --query 'length(ProcessingJobSummaries)' --output text"
  echo ""
  if [ "$fail" = "0" ]; then
    echo "==> Lab 5 teardown complete. No billable inference resources remain."
    echo "    Model Registry entries are metadata only and cost nothing - kept."
    echo "    Run scripts/teardown-lab2.sh as well if you are done with the data platform."
  else
    echo "==> WARNING: resources above may still be billing."
    exit 1
  fi
} | tee docs/lab5-teardown-output.txt
