# NorthStar alert set (Lab 6 Tasks 1 and 3). Every name contains var.project.
# Severity tiers, escalation and the suppression rule are described in
# ALERTS.md in this directory. Each alarm carries its tier in a Severity tag
# and at the start of its description.
#
# Routing: every tier publishes to the one topic in main.tf. Alarms 2, 5 and 6
# (latency and CPU) do not notify directly; they notify through the serving
# composite alarms in suppression.tf so that deployments and outages can
# suppress them.

locals {
  endpoint_dims = { EndpointName = var.endpoint_name }
  variant_dims = {
    EndpointName = var.endpoint_name
    VariantName  = var.monitored_variant
  }
  alarm_actions = [aws_sns_topic.alerts.arn]
}

# 1. P1, serving availability: more than 1% of requests fail with a 5XX error.
# 1% is twice the 0.5% failure rate the 99.5% availability SLO allows.
# Both variants are counted, so a failing canary also pages.
resource "aws_cloudwatch_metric_alarm" "error_rate_high" {
  alarm_name          = "${var.project}-churn-5xx-error-rate-high"
  alarm_description   = "P1 Serving: more than 1% of endpoint requests returned a 5XX error over 5 minutes"
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Severity = "P1", Layer = "serving" }

  metric_query {
    id          = "rate"
    label       = "5XX error rate (%)"
    return_data = true
    expression  = "100 * (FILL(e1,0) + FILL(e2,0)) / IF((FILL(i1,0) + FILL(i2,0)) > 0, FILL(i1,0) + FILL(i2,0), 1)"
  }

  dynamic "metric_query" {
    for_each = {
      e1 = ["Invocation5XXErrors", "champion"]
      i1 = ["Invocations", "champion"]
      e2 = ["Invocation5XXErrors", "canary"]
      i2 = ["Invocations", "canary"]
    }
    content {
      id = metric_query.key
      metric {
        namespace   = "AWS/SageMaker"
        metric_name = metric_query.value[0]
        dimensions  = { EndpointName = var.endpoint_name, VariantName = metric_query.value[1] }
        period      = 300
        stat        = "Sum"
      }
    }
  }
}

# 2. P2, serving: requests are badly slow (handout runbook B threshold).
# ModelLatency is in microseconds: 500 ms = 500000. Notifies via suppression.tf.
resource "aws_cloudwatch_metric_alarm" "latency_p95_critical" {
  alarm_name          = "${var.project}-churn-latency-p95-critical"
  alarm_description   = "P2 Serving: ${var.monitored_variant} p95 ModelLatency above 500 ms (500000 us) over 5 minutes"
  namespace           = "AWS/SageMaker"
  metric_name         = "ModelLatency"
  dimensions          = local.variant_dims
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 1
  threshold           = 500000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
  tags                = { Severity = "P2", Layer = "serving" }
}

# 3. P2, pipeline: the Glue failure rule is in main.tf.

# 4. P2, business: churn alerts handed to retention fell sharply versus the
# trailing average, which usually means the model or its inputs broke.
resource "aws_cloudwatch_metric_alarm" "alert_volume_drop" {
  alarm_name          = "${var.project}-churn-alert-volume-drop"
  alarm_description   = "P2 Business: daily churn-alert volume more than 30% below the mean of the prior days"
  namespace           = var.metric_namespace
  metric_name         = "ChurnAlertVolumeChangePct"
  dimensions          = local.endpoint_dims
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = -30
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Severity = "P2", Layer = "business" }
}

# 5. P3, serving: early latency warning. Notifies via suppression.tf.
resource "aws_cloudwatch_metric_alarm" "latency_p95_high" {
  alarm_name          = "${var.project}-churn-latency-p95-high"
  alarm_description   = "P3 Serving: ${var.monitored_variant} p95 ModelLatency above 200 ms (200000 us) over 5 minutes"
  namespace           = "AWS/SageMaker"
  metric_name         = "ModelLatency"
  dimensions          = local.variant_dims
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 1
  threshold           = 200000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
  tags                = { Severity = "P3", Layer = "serving" }
}

# 6. P3, infrastructure: the serving instance is CPU-saturated.
# Notifies via suppression.tf.
resource "aws_cloudwatch_metric_alarm" "cpu_p90_high" {
  alarm_name          = "${var.project}-churn-cpu-p90-high"
  alarm_description   = "P3 Infrastructure: ${var.monitored_variant} instance CPU p90 above 80% over 5 minutes"
  namespace           = "/aws/sagemaker/Endpoints"
  metric_name         = "CPUUtilization"
  dimensions          = local.variant_dims
  extended_statistic  = "p90"
  period              = 300
  evaluation_periods  = 1
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
  tags                = { Severity = "P3", Layer = "infrastructure" }
}

# 7. P3, model quality: the latest drift job flagged at least one feature.
# Published only when publish_metrics.py runs, so no data means no new result.
resource "aws_cloudwatch_metric_alarm" "drift_detected" {
  alarm_name          = "${var.project}-churn-drift-detected"
  alarm_description   = "P3 Model quality: the latest Evidently drift job flagged one or more features (DriftDetected >= 1)"
  namespace           = var.metric_namespace
  metric_name         = "DriftDetected"
  dimensions          = local.endpoint_dims
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Severity = "P3", Layer = "model-quality" }
}

# 8. P4, deployment: the canary rollback trigger created by
# deployment/configs/canary_deploy_realtime.py, adopted into Terraform with its
# definition unchanged, including no actions and no tags: the deploy script
# reads its state rather than being paged by it. Its P4 tier (ticket only) is
# recorded in ALERTS.md.
import {
  to = aws_cloudwatch_metric_alarm.rollback_latency
  id = "northstar-churn-rollback-latency"
}

resource "aws_cloudwatch_metric_alarm" "rollback_latency" {
  alarm_name          = "northstar-churn-rollback-latency"
  alarm_description   = "Rollback trigger: churn canary p95 ModelLatency above 200 ms (200000 microseconds) for 2 consecutive minutes"
  namespace           = "AWS/SageMaker"
  metric_name         = "ModelLatency"
  dimensions          = { EndpointName = var.endpoint_name, VariantName = "canary" }
  extended_statistic  = "p95"
  period              = 60
  evaluation_periods  = 2
  threshold           = 200000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}
