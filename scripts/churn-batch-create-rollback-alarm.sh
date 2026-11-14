#!/usr/bin/env bash
# Churn batch scoring: create (or update in place) the rollback alarm from its
# definition in deployment/configs/rollback-alarm.json.
#
# Usage: bash scripts/churn-batch-create-rollback-alarm.sh
source "$(dirname "$0")/churn-batch-common.sh"

aws cloudwatch put-metric-alarm --cli-input-json "file://${ALARM_CFG}"
aws cloudwatch describe-alarms --alarm-names "$(jq -r .AlarmName "$ALARM_CFG")" \
  --query 'MetricAlarms[].[AlarmName,Namespace,MetricName,Statistic,Threshold,Unit,ComparisonOperator,StateValue]' \
  --output table
