#!/usr/bin/env bash
# preflight-lab6.sh
# Run this BEFORE you launch the Evidently drift job in Lab 6.
#
# WHY THIS EXISTS
#
# Evidently analyses inference data that the endpoint captured to S3. If
# DataCaptureConfig was not enabled when the endpoint was created, there is
# nothing to analyse. The job is accepted, runs, and produces an empty or
# schema-confused report while the endpoint bills the entire time.
#
# (SageMaker Model Monitor SCHEDULES are closed to new AWS accounts, which is
# why this lab uses Evidently in a plain processing job instead.)
#
# Endpoint configs are IMMUTABLE. Capture cannot be switched on for a running
# endpoint. If this script fails you must create a new endpoint config with
# capture enabled and call update-endpoint (about 3 min 47 s), which is exactly
# what deployment/configs/canary_deploy_realtime.py now does.
#
# Earlier versions of Lab 5 deployed without capture. If you deployed under the
# old spec, this script is how you find out before it costs you an hour.
#
# Usage: bash scripts/preflight-lab6.sh

set -uo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
PROJECT="${PROJECT:-northstar}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
P="${PROJECT}-${ENVIRONMENT}"
ENDPOINT="${ENDPOINT_NAME:-northstar-churn-prod}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
BUCKET="${P}-data-${ACCOUNT_ID}"

PASS=0; FAIL=0
ok ()   { printf "  \033[32mPASS\033[0m  %-52s %s\n" "$1" "${2:-}"; PASS=$((PASS+1)); }
bad ()  { printf "  \033[31mFAIL\033[0m  %-52s %s\n" "$1" "${2:-}"; FAIL=$((FAIL+1)); }
warn () { printf "  \033[33mWARN\033[0m  %-52s %s\n" "$1" "${2:-}"; }
head2 () { printf "\n\033[1m%s\033[0m\n" "$1"; }

echo "NorthStar Lab 6 pre-flight - account ${ACCOUNT_ID}, region ${REGION}"

# ── 1. Endpoint is live ────────────────────────────────────────────────────────
head2 "1 - Endpoint"

STATUS=$(aws sagemaker describe-endpoint --endpoint-name "${ENDPOINT}" \
  --region "${REGION}" --query 'EndpointStatus' --output text 2>/dev/null)
if [ "${STATUS}" = "InService" ]; then
  ok "endpoint InService" "${ENDPOINT}"
else
  bad "endpoint InService" "${ENDPOINT} is '${STATUS:-not found}'"
  echo
  echo "  Deploy Lab 5's endpoint first. There is nothing to monitor without it."
  exit 1
fi

CONFIG=$(aws sagemaker describe-endpoint --endpoint-name "${ENDPOINT}" \
  --region "${REGION}" --query 'EndpointConfigName' --output text 2>/dev/null)

# ── 2. Data capture — the blocker this script exists for ───────────────────────
head2 "2 - Data capture (blocks Task 1 entirely)"

CAPTURE=$(aws sagemaker describe-endpoint-config --endpoint-config-name "${CONFIG}" \
  --region "${REGION}" --query 'DataCaptureConfig.EnableCapture' --output text 2>/dev/null)

if [ "${CAPTURE}" = "True" ]; then
  ok "DataCaptureConfig enabled" "config ${CONFIG}"

  PCT=$(aws sagemaker describe-endpoint-config --endpoint-config-name "${CONFIG}" \
    --region "${REGION}" --query 'DataCaptureConfig.InitialSamplingPercentage' --output text)
  DEST=$(aws sagemaker describe-endpoint-config --endpoint-config-name "${CONFIG}" \
    --region "${REGION}" --query 'DataCaptureConfig.DestinationS3Uri' --output text)
  MODES=$(aws sagemaker describe-endpoint-config --endpoint-config-name "${CONFIG}" \
    --region "${REGION}" --query 'DataCaptureConfig.CaptureOptions[].CaptureMode' --output text)

  ok "sampling percentage" "${PCT}%"
  ok "capture destination" "${DEST}"

  case "${MODES}" in
    *Input*Output*|*InputAndOutput*) ok "capture modes" "${MODES}" ;;
    *) bad "capture modes" "${MODES} - drift analysis needs Input" ;;
  esac
else
  bad "DataCaptureConfig enabled" "config ${CONFIG} has capture OFF"
  cat <<'EOF'

  ────────────────────────────────────────────────────────────────────────
  STOP. Do not launch the drift job yet.

  This endpoint captures no inference data, so every Evidently run
  will produce nothing while the endpoint bills at $0.115/hr.

  Endpoint configs are immutable, so fix it by rolling a new one:

    python deployment/configs/canary_deploy_realtime.py \
      --model-package-arn <arn> --role-arn <arn>

  That creates a capture-enabled config and calls update-endpoint
  (~3 min 47 s, zero downtime). Re-run this script afterwards.
  ────────────────────────────────────────────────────────────────────────
EOF
  exit 1
fi

# ── 3. Captured objects actually landing ───────────────────────────────────────
head2 "3 - Captured objects in S3"

# Capture is written asynchronously and lands under
#   <prefix>/<endpoint>/<variant>/<yyyy>/<mm>/<dd>/<hh>/*.jsonl
# It can lag several minutes behind the first invocation. An empty result here
# is NOT necessarily a failure if you have not invoked the endpoint yet.
COUNT=$(aws s3 ls "s3://${BUCKET}/datacapture/${ENDPOINT}/" --recursive 2>/dev/null | wc -l | tr -d ' ')
if [ "${COUNT}" -gt 0 ]; then
  ok "captured objects present" "${COUNT} object(s)"
else
  warn "no captured objects yet" "invoke the endpoint, then wait a few minutes"
  echo "        Capture is asynchronous. Baseline against an empty prefix and the"
  echo "        job fails with a confusing schema error, not an empty-input error."
fi

# ── 3b. Processing quota — CreateProcessingJob is rejected outright at 0 ───────
head2 "3b - Processing-job quota"

# Default quota for every non-burstable processing instance is 0. This is not a
# soft limit you can grow into; the CreateProcessingJob call is rejected outright
# with ResourceLimitExceeded. Only ml.t3.* have non-zero defaults.
MON_INSTANCE="${MON_INSTANCE:-ml.t3.medium}"

# NOTE: list-service-quotas paginates, and the CLI applies --query PER PAGE.
# A filter like `...Value | [0]` therefore emits one line per page — mostly
# "None" plus the real value buried somewhere in the middle. Use the quota CODE
# with get-service-quota, which returns exactly one value and no pagination.
quota_for () {  # $1 = quota code
  aws service-quotas get-service-quota --service-code sagemaker \
    --quota-code "$1" --query 'Quota.Value' --output text --region "${REGION}" 2>/dev/null
}
case "${MON_INSTANCE}" in
  ml.t3.medium) MON_CODE="L-0CE343FE" ;;
  ml.t3.large)  MON_CODE="L-C076FA77" ;;
  ml.t3.xlarge) MON_CODE="L-DABA7ED5" ;;
  ml.m5.large)  MON_CODE="L-8541302D" ;;
  *)            MON_CODE="" ;;
esac

if [ -z "${MON_CODE}" ]; then
  warn "unknown instance ${MON_INSTANCE}" "cannot check quota; use ml.t3.medium"
else
  Q=$(quota_for "${MON_CODE}")
  if [ -n "${Q}" ] && [ "${Q}" != "None" ] && [ "${Q%.*}" -gt 0 ] 2>/dev/null; then
    ok "quota for ${MON_INSTANCE}" "${Q%.*} instance(s)"
  else
    bad "quota for ${MON_INSTANCE}" "${Q:-0} - CreateProcessingJob will be rejected"
  fi
fi

# Evidently is pandas, not Spark, so 4 GB is enough. Verified 2026-08-07:
# 10,000 baseline rows vs 800 captured records completed in 1 min 59 s billed.
# (The retired Model Monitor analyzer WAS Spark and OOM'd here after 13 min 43 s.)
if [ "${MON_INSTANCE}" = "ml.t3.large" ]; then
  echo "  note: ml.t3.large works but costs 2x ml.t3.medium for no benefit."
  echo "        Evidently is pandas, not Spark. 4 GB is sufficient."
fi

QM5=$(quota_for "L-8541302D")
if [ "${QM5%.*}" -eq 0 ] 2>/dev/null; then
  echo "  note: ml.m5.large processing quota is ${QM5:-0}. That is the AWS DEFAULT."
  echo "        Endpoint, training and processing quotas are three different numbers,"
  echo "        and AWS raises an account's APPLIED quotas as it accrues usage history."
  echo "        A brand-new account starts at 0 for all three on-demand families."
fi

# ── 4. Execution role — the second Lab 6 blocker ───────────────────────────────
head2 "4 - Model Monitor execution role"

EXEC_ROLE="${P}-ModelMonitorExecution"
ROLE_ARN=$(aws iam get-role --role-name "${EXEC_ROLE}" --query 'Role.Arn' --output text 2>/dev/null)

if [ -n "${ROLE_ARN}" ] && [ "${ROLE_ARN}" != "None" ]; then
  ok "execution role exists" "${EXEC_ROLE}"
  echo "        Pass this as --role-arn to run_evidently_job.py:"
  echo "        ${ROLE_ARN}"
else
  bad "execution role exists" "${EXEC_ROLE} not found - add it to modules/iam (Lab 6 Prerequisite 2), then terraform apply"
  echo
  echo "  Do NOT substitute ${P}-ModelMonitor. That role is a read-only observer:"
  echo "  no S3 write, no ECR pull. A drift job using it fails several minutes"
  echo "  into the job, as an opaque ProcessingJobStatus Failed."
fi

# ── 5. Cost guard ──────────────────────────────────────────────────────────────
head2 "5 - Cost"

CREATED=$(aws sagemaker describe-endpoint --endpoint-name "${ENDPOINT}" \
  --region "${REGION}" --query 'CreationTime' --output text 2>/dev/null)
EP_INSTANCE=$(aws sagemaker describe-endpoint-config --endpoint-config-name "${CONFIG}" \
  --region "${REGION}" --query 'ProductionVariants[0].InstanceType' --output text 2>/dev/null)
NVARIANTS=$(aws sagemaker describe-endpoint-config --endpoint-config-name "${CONFIG}" \
  --region "${REGION}" --query 'length(ProductionVariants)' --output text 2>/dev/null)

# Burn rate depends on which instance Lab 5 left you on, and on how many
# variants are still live — a two-variant canary bills BOTH.
case "${EP_INSTANCE}" in
  ml.t2.medium) RATE=0.056  ;;
  ml.m5.large)  RATE=0.115  ;;
  ml.m6g.large) RATE=0.0924 ;;
  *)            RATE=0      ;;
esac

echo "  Endpoint created:  ${CREATED}"
echo "  Instance:          ${EP_INSTANCE} x ${NVARIANTS} variant(s)"
if [ "${RATE}" != "0" ]; then
  BURN=$(awk -v r="${RATE}" -v n="${NVARIANTS}" 'BEGIN{printf "%.4f", r*n + (1.983/60)*0.05 + 6*0.10/730}')
  HOURS=$(awk -v b="${BURN}" 'BEGIN{printf "%.0f", (10-0.30)/b}')
  echo "  Burn rate:         \$${BURN}/hr (endpoint + hourly ml.t3.medium drift job)"
  echo "  Budget alarm:      \$10/mo breaches after ~${HOURS} hours left running"
else
  echo "  Burn rate:         unknown instance type ${EP_INSTANCE} - check pricing"
fi
echo "  Measured:          Evidently drift job 1 min 59 s billed, \$0.0017 on ml.t3.medium"
if [ "${NVARIANTS}" -gt 1 ]; then
  warn "two-variant canary still live" "you are paying for ${NVARIANTS} instances"
  echo "        Promote or roll back, then delete the losing variant."
fi
echo
echo "  Any timer you build keeps launching billable jobs after you delete the"
echo "  endpoint. Tear down with scripts/teardown-lab6.sh, not teardown-lab5.sh."

# An MLflow tracking server left over from Lab 3 bills $0.60/hr -- more than
# the endpoint this lab is warning you about. Surface it while the student is
# already thinking about cost. The serverless MLflow App is free; not checked.
MLTS=$(aws sagemaker list-mlflow-tracking-servers --region "${REGION}" \
  --query 'length(TrackingServerSummaries)' --output text 2>/dev/null)
case "${MLTS:-0}" in ''|*[!0-9]*) MLTS=0 ;; esac
if [ "${MLTS}" -gt 0 ] 2>/dev/null; then
  echo
  bad "MLflow TRACKING SERVER running" "${MLTS} - billing \$0.60/hr"
  echo "        That is more per hour than this lab's endpoint. Left a week it is"
  echo "        ~\$100 and breaches the \$10 course budget in under 17 hours."
  echo "        You want the serverless MLflow APP (free), not a tracking server."
  echo "        aws sagemaker delete-mlflow-tracking-server --tracking-server-name <name>"
fi

# ── Summary ────────────────────────────────────────────────────────────────────
printf "\n\033[1mPre-flight: %d passed, %d failed\033[0m\n" "${PASS}" "${FAIL}"
[ "${FAIL}" -eq 0 ] || exit 1
echo "Clear to launch the Evidently drift job."
