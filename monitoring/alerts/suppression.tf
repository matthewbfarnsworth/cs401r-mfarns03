# Suppression rule (Lab 6 Task 3): while a deployment is in progress, or while
# the P1 5XX error-rate alarm is firing, hold back the latency and CPU alerts.
#
# Deployments cause cold starts and instance replacement, which spike latency
# and CPU on a healthy rollout. During an outage, the error-rate page already
# names the root cause, and the latency and CPU alerts it drags along add noise.
#
# Mechanism: the latency and CPU alarms have no actions of their own. Two
# composite alarms (one per tier) watch them and send the notifications, and
# both name the suppressor composite below as their actions suppressor.
#
# Teardown: scripts/teardown-lab6.sh deletes northstar composite alarms before
# the metric alarms they reference.

# Signal for "a deployment is in progress". canary_deploy_realtime.py publishes
# DeploymentInProgress = 1 about every 30 s while it creates or updates the
# endpoint. When publishing stops, CloudWatch waits several minutes for late
# data (about 7 in testing) and then treats the missing data as not breaching,
# so the alarm returns to OK by itself.
resource "aws_cloudwatch_metric_alarm" "deployment_in_progress" {
  alarm_name          = "${var.project}-churn-deployment-in-progress"
  alarm_description   = "Suppression signal: an endpoint deployment is in progress (no notifications)"
  namespace           = var.metric_namespace
  metric_name         = "DeploymentInProgress"
  dimensions          = local.endpoint_dims
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  tags                = { Severity = "none", Layer = "suppression" }
}

# In ALARM when either suppression condition holds. Sends nothing itself.
resource "aws_cloudwatch_composite_alarm" "serving_suppressor" {
  alarm_name        = "${var.project}-churn-serving-suppressor"
  alarm_description = "Suppression condition: deployment in progress OR P1 5XX error rate firing (no notifications)"
  alarm_rule        = "ALARM(\"${aws_cloudwatch_metric_alarm.deployment_in_progress.alarm_name}\") OR ALARM(\"${aws_cloudwatch_metric_alarm.error_rate_high.alarm_name}\")"
  tags              = { Severity = "none", Layer = "suppression" }
}

locals {
  # Wait up to one 5-minute evaluation period of the error-rate alarm for the
  # suppressor to catch up, so an outage that starts together with the latency
  # spike still suppresses it. After the suppressor clears, keep suppressing
  # for two 5-minute periods so the first post-deployment window, which still
  # contains cold-start requests, does not notify.
  suppressor_wait_seconds      = 300
  suppressor_extension_seconds = 600
}

resource "aws_cloudwatch_composite_alarm" "serving_p2" {
  alarm_name        = "${var.project}-churn-serving-p2"
  alarm_description = "P2 Serving notification for the p95 latency critical alarm, suppressed during deployments and P1 outages"
  alarm_rule        = "ALARM(\"${aws_cloudwatch_metric_alarm.latency_p95_critical.alarm_name}\")"
  alarm_actions     = local.alarm_actions
  ok_actions        = local.alarm_actions
  tags              = { Severity = "P2", Layer = "serving" }

  actions_suppressor {
    alarm            = aws_cloudwatch_composite_alarm.serving_suppressor.alarm_name
    wait_period      = local.suppressor_wait_seconds
    extension_period = local.suppressor_extension_seconds
  }
}

resource "aws_cloudwatch_composite_alarm" "serving_p3" {
  alarm_name        = "${var.project}-churn-serving-p3"
  alarm_description = "P3 Serving and infrastructure notification for the p95 latency warning and CPU alarms, suppressed during deployments and P1 outages"
  alarm_rule        = "ALARM(\"${aws_cloudwatch_metric_alarm.latency_p95_high.alarm_name}\") OR ALARM(\"${aws_cloudwatch_metric_alarm.cpu_p90_high.alarm_name}\")"
  alarm_actions     = local.alarm_actions
  ok_actions        = local.alarm_actions
  tags              = { Severity = "P3", Layer = "serving" }

  actions_suppressor {
    alarm            = aws_cloudwatch_composite_alarm.serving_suppressor.alarm_name
    wait_period      = local.suppressor_wait_seconds
    extension_period = local.suppressor_extension_seconds
  }
}
