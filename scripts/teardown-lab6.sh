#!/usr/bin/env bash
#
# teardown-lab6.sh
#
# Removes everything Lab 6 creates, in the order that actually works, and then
# proves the account is clean.
#
# WHY THIS IS SEPARATE FROM teardown-lab5.sh
#
# teardown-lab5.sh does not know about monitoring schedules or monitoring
# processing jobs. On an account that CAN create monitoring schedules, a
# schedule left behind keeps launching billable processing jobs on its own
# cadence long after the endpoint it pointed at is gone. That is the single
# most expensive way to fail this lab.
#
# ORDER MATTERS: delete the monitoring schedule BEFORE the endpoint. A schedule
# whose endpoint has vanished still fires; it just fails each time, and you are
# billed for the instance each failed job spins up.
#
# Usage: bash scripts/teardown-lab6.sh
set -uo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
PROJECT="${PROJECT:-northstar}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
P="${PROJECT}-${ENVIRONMENT}"
ENDPOINT="${ENDPOINT_NAME:-northstar-churn-prod}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
BUCKET="${P}-data-${ACCOUNT_ID}"
OUT="docs/lab6-teardown-output.txt"

mkdir -p docs
exec > >(tee "${OUT}") 2>&1

echo "NorthStar Lab 6 teardown - account ${ACCOUNT_ID}, region ${REGION}"
echo "started $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

step() { printf "\n\033[1m%s\033[0m\n" "$1"; }
ok ()  { printf "  \033[32mOK\033[0m    %s\n" "$1"; }
warn() { printf "  \033[33mWARN\033[0m  %s\n" "$1"; }

# ── 1. Monitoring schedules FIRST ─────────────────────────────────────────────
step "1 - Monitoring schedules (delete before the endpoint)"
SCHEDULES=$(aws sagemaker list-monitoring-schedules --region "${REGION}" \
  --query 'MonitoringScheduleSummaries[].MonitoringScheduleName' --output text 2>/dev/null)
if [ -z "${SCHEDULES}" ]; then
  ok "none found"
else
  for S in ${SCHEDULES}; do
    aws sagemaker delete-monitoring-schedule --monitoring-schedule-name "$S" --region "${REGION}" 2>/dev/null \
      && ok "deleted schedule $S" || warn "could not delete schedule $S"
  done
  sleep 10
fi

# ── 2. Monitoring processing jobs still running ───────────────────────────────
step "2 - In-flight monitoring processing jobs"
RUNNING=$(aws sagemaker list-processing-jobs --region "${REGION}" --status-equals InProgress \
  --query 'ProcessingJobSummaries[].ProcessingJobName' --output text 2>/dev/null)
if [ -z "${RUNNING}" ]; then
  ok "none running"
else
  for J in ${RUNNING}; do
    aws sagemaker stop-processing-job --processing-job-name "$J" --region "${REGION}" 2>/dev/null \
      && ok "stopped $J" || warn "could not stop $J"
  done
fi

# ── 3. Auto-scaling ───────────────────────────────────────────────────────────
# Deleting the endpoint deregisters these automatically (verified 2026-07-30 —
# they were gone within 90 seconds). Doing it explicitly keeps the run
# deterministic and makes the verification block below meaningful when a
# student registered a target by hand.
step "3 - Auto-scaling targets"
TARGETS=$(aws application-autoscaling describe-scalable-targets --service-namespace sagemaker \
  --region "${REGION}" --query 'ScalableTargets[].ResourceId' --output text 2>/dev/null)
if [ -z "${TARGETS}" ]; then
  ok "none registered"
else
  for T in ${TARGETS}; do
    aws application-autoscaling deregister-scalable-target --service-namespace sagemaker \
      --resource-id "$T" --scalable-dimension sagemaker:variant:DesiredInstanceCount \
      --region "${REGION}" 2>/dev/null && ok "deregistered $T" || warn "could not deregister $T"
  done
fi

# ── 4. Endpoint, config, models ───────────────────────────────────────────────
step "4 - Endpoint and its dependencies"
if aws sagemaker describe-endpoint --endpoint-name "${ENDPOINT}" --region "${REGION}" >/dev/null 2>&1; then
  CFG=$(aws sagemaker describe-endpoint --endpoint-name "${ENDPOINT}" --region "${REGION}" \
    --query EndpointConfigName --output text)
  aws sagemaker delete-endpoint --endpoint-name "${ENDPOINT}" --region "${REGION}" 2>/dev/null \
    && ok "deleted endpoint ${ENDPOINT} (this is what stops the hourly charge)"
  aws sagemaker delete-endpoint-config --endpoint-config-name "${CFG}" --region "${REGION}" 2>/dev/null \
    && ok "deleted endpoint config ${CFG}"
else
  ok "endpoint ${ENDPOINT} not present"
fi

for M in $(aws sagemaker list-models --region "${REGION}" \
           --query 'Models[?starts_with(ModelName,`northstar-`)].ModelName' --output text 2>/dev/null); do
  aws sagemaker delete-model --model-name "$M" --region "${REGION}" 2>/dev/null && ok "deleted model $M"
done

# ── 5. CloudWatch alarms ──────────────────────────────────────────────────────
step "5 - CloudWatch alarms"
ALARMS=$(aws cloudwatch describe-alarms --region "${REGION}" \
  --query 'MetricAlarms[?contains(AlarmName,`northstar`) || starts_with(AlarmName,`TargetTracking`)].AlarmName' \
  --output text 2>/dev/null)
if [ -z "${ALARMS}" ]; then
  ok "none found"
else
  # shellcheck disable=SC2086
  aws cloudwatch delete-alarms --alarm-names ${ALARMS} --region "${REGION}" 2>/dev/null \
    && ok "deleted: ${ALARMS}"
fi

# ── 5b. Anomaly detectors — a NEW orphan class ────────────────────────────────
#
# An alarm using ANOMALY_DETECTION_BAND causes CloudWatch to create an anomaly
# detector implicitly. Terraform does NOT track it: `terraform destroy` removed
# all five alarms on 2026-07-31 and left the detector behind, still TRAINED.
# Deleting the alarm does not delete the detector either.
#
# This is a new member of the "terraform destroy does not fully tear down"
# family. It is cheap but it is not free, and it is invisible unless you go
# looking for it.
step "5b - CloudWatch anomaly detectors (orphaned by terraform destroy)"
DETECTORS=$(aws cloudwatch describe-anomaly-detectors --region "${REGION}" \
  --query 'AnomalyDetectors[?contains(Namespace,`NorthStar`)].[Namespace,MetricName,Stat]' \
  --output text 2>/dev/null)
if [ -z "${DETECTORS}" ]; then
  ok "none found"
else
  echo "${DETECTORS}" | while read -r NS MN STAT; do
    [ -z "${NS}" ] && continue
    aws cloudwatch delete-anomaly-detector --region "${REGION}" \
      --namespace "${NS}" --metric-name "${MN}" --stat "${STAT}" \
      --dimensions Name=EndpointName,Value="${ENDPOINT}" 2>/dev/null \
      && ok "deleted anomaly detector ${NS}/${MN}" \
      || warn "could not delete anomaly detector ${NS}/${MN}"
  done
fi

# ── 6. Captured data (report only) ────────────────────────────────────────────
# Not deleted: the datacapture/ lifecycle rule expires objects after 7 days, and
# the captured records are evidence a grader may want to see. S3 storage for a
# lab-sized capture is fractions of a cent.
step "6 - Captured inference data (retained by design)"
CAP=$(aws s3 ls "s3://${BUCKET}/datacapture/" --recursive 2>/dev/null | grep -c jsonl || true)
ok "${CAP:-0} capture object(s) left in s3://${BUCKET}/datacapture/ (7-day lifecycle)"
REP=$(aws s3 ls "s3://${BUCKET}/monitoring/" --recursive 2>/dev/null | wc -l | tr -d ' ')
ok "${REP} monitoring artifact(s) retained under s3://${BUCKET}/monitoring/"

# ── 7. Verification sweep ─────────────────────────────────────────────────────
step "7 - Verification (independent all-region sweep)"
sleep 15
FAIL=0
# --query applies PER PAGE: a paginated list prints one length per page
# ("0" and "0" on two lines), which never equals "0". Sum the pages.
# Found 2026-11-18 when a clean us-east-1 failed this check.
sum_pages() { awk '{for(i=1;i<=NF;i++) s+=$i} END {print s+0}'; }
for R in us-east-1 us-east-2 us-west-1 us-west-2 eu-west-1 eu-central-1 ap-southeast-1 ap-northeast-1; do
  E=$(aws sagemaker list-endpoints --region "$R" --query 'length(Endpoints)' --output text 2>/dev/null | sum_pages)
  S=$(aws sagemaker list-monitoring-schedules --region "$R" --query 'length(MonitoringScheduleSummaries)' --output text 2>/dev/null | sum_pages)
  J=$(aws sagemaker list-processing-jobs --region "$R" --status-equals InProgress --query 'length(ProcessingJobSummaries)' --output text 2>/dev/null | sum_pages)
  # Anomaly detectors are included here because describe-anomaly-detectors is
  # EVENTUALLY CONSISTENT: on 2026-07-31 a detector reported deleted showed up
  # again moments later in PENDING_TRAINING before settling. Step 5b can
  # legitimately report "none found" while one is still surfacing, so the sweep
  # has to check independently rather than trust the delete.
  D=$(aws cloudwatch describe-anomaly-detectors --region "$R" --query 'length(AnomalyDetectors)' --output text 2>/dev/null | sum_pages)
  # MLflow TRACKING SERVERS bill $0.60/hr from creation to deletion and are not
  # endpoints, so nothing else in this course catches them. $10 budget breaches
  # in 16.7 hours. The serverless MLflow APP is free and is NOT checked here --
  # students are meant to keep it. Old CLIs lack this command; treat as 0.
  M=$(aws sagemaker list-mlflow-tracking-servers --region "$R" --query 'length(TrackingServerSummaries)' --output text 2>/dev/null | sum_pages)
  M=${M:-0}; case "$M" in ''|*[!0-9]*) M=0 ;; esac
  if [ "${E:-0}${S:-0}${J:-0}${D:-0}${M}" = "00000" ]; then
    ok "$R clean"
  else
    warn "$R endpoints=$E schedules=$S running-jobs=$J anomaly-detectors=$D mlflow-tracking-servers=$M"
    [ "$M" != "0" ] && echo "        MLflow tracking server in $R is billing \$0.60/hr RIGHT NOW."
    [ "$M" != "0" ] && echo "        aws sagemaker delete-mlflow-tracking-server --tracking-server-name <name> --region $R"
    FAIL=$((FAIL+1))
  fi
done

echo
if [ "${FAIL}" -eq 0 ]; then
  printf "\033[32m\033[1mTEARDOWN COMPLETE - nothing is billing hourly.\033[0m\n"
else
  printf "\033[31m\033[1m%s region(s) still have live resources. Investigate before you submit.\033[0m\n" "${FAIL}"
fi
echo "finished $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "evidence written to ${OUT}"
[ "${FAIL}" -eq 0 ] || exit 1
