# NorthStar Task 1 alarms, one per monitored layer (the pipeline layer's alert
# is the EventBridge rule in main.tf). Every name contains var.project.
# All new alarms use one 5-minute period, 1 of 1 evaluation periods.

locals {
  endpoint_dims = { EndpointName = var.endpoint_name }
  variant_dims = {
    EndpointName = var.endpoint_name
    VariantName  = var.monitored_variant
  }
  alarm_actions = [aws_sns_topic.alerts.arn]
}

# Infrastructure layer: the serving instance is CPU-saturated.
resource "aws_cloudwatch_metric_alarm" "cpu_p90_high" {
  alarm_name          = "${var.project}-churn-cpu-p90-high"
  alarm_description   = "Infrastructure: ${var.monitored_variant} instance CPU p90 above 80% over 5 minutes"
  namespace           = "/aws/sagemaker/Endpoints"
  metric_name         = "CPUUtilization"
  dimensions          = local.variant_dims
  extended_statistic  = "p90"
  period              = 300
  evaluation_periods  = 1
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# Model-serving layer: requests are slow. ModelLatency is in microseconds.
resource "aws_cloudwatch_metric_alarm" "latency_p95_high" {
  alarm_name          = "${var.project}-churn-latency-p95-high"
  alarm_description   = "Serving: ${var.monitored_variant} p95 ModelLatency above 200 ms (200000 us) over 5 minutes"
  namespace           = "AWS/SageMaker"
  metric_name         = "ModelLatency"
  dimensions          = local.variant_dims
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 1
  threshold           = 200000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# Model-quality layer: the latest drift job flagged at least one feature.
# Published only when publish_metrics.py runs, so no data means no new result.
resource "aws_cloudwatch_metric_alarm" "drift_detected" {
  alarm_name          = "${var.project}-churn-drift-detected"
  alarm_description   = "Model quality: the latest Evidently drift job flagged one or more features (DriftDetected >= 1)"
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
}

# Business layer: churn alerts handed to retention fell sharply versus the
# trailing average, which usually means the model or its inputs broke, not
# that customers stopped churning.
resource "aws_cloudwatch_metric_alarm" "alert_volume_drop" {
  alarm_name          = "${var.project}-churn-alert-volume-drop"
  alarm_description   = "Business: daily churn-alert volume more than 30% below the mean of the prior days"
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
}

# The canary rollback trigger created by deployment/configs/canary_deploy_realtime.py.
# Adopted into Terraform with its definition unchanged, including no actions:
# the deploy script reads its state rather than being paged by it.
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
