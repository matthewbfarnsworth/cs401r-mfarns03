# NorthStar-AI-Platform: one widget per monitored layer, plus alarm status.
# Export after apply:
#   aws cloudwatch get-dashboard --dashboard-name NorthStar-AI-Platform \
#     --query DashboardBody --output text | python -m json.tool \
#     > monitoring/dashboards/northstar-dashboard.json

locals {
  ep  = var.endpoint_name
  var = var.monitored_variant
  ns  = var.metric_namespace

  infra_metrics = [
    for m in ["CPUUtilization", "MemoryUtilization", "DiskUtilization"] :
    ["/aws/sagemaker/Endpoints", m, "EndpointName", local.ep, "VariantName", local.var]
  ]

  serving_metrics = concat(
    [for p in ["p50", "p95", "p99"] :
      ["AWS/SageMaker", "ModelLatency", "EndpointName", local.ep, "VariantName", local.var,
    { stat = p, label = "ModelLatency ${p} (us)" }]],
    [for m in ["Invocations", "Invocation4XXErrors", "Invocation5XXErrors"] :
      ["AWS/SageMaker", m, "EndpointName", local.ep, "VariantName", local.var,
    { stat = "Sum", yAxis = "right" }]],
  )

  quality_metrics = concat(
    [for m in ["DriftDetected", "ViolationCount"] :
    [local.ns, m, "EndpointName", local.ep, { stat = "Maximum" }]],
    [for p in local.drift_pairs :
      [local.ns, p.metric, "EndpointName", local.ep, "Feature", p.feature,
    { stat = "Maximum", yAxis = "right", label = "${p.metric} ${p.feature}" }]],
  )

  # flatten() over objects, not arrays, so the metric arrays above stay arrays.
  drift_pairs = flatten([for metric, features in var.drift_features :
  [for f in features : { metric = metric, feature = f }]])

  pipeline_metrics = concat(
    [for j in var.glue_job_names :
      ["Glue", "glue.driver.aggregate.elapsedTime", "JobName", j, "JobRunId", "ALL", "Type", "count",
    { stat = "Maximum", label = "${j} elapsed (ms)" }]],
    [for j in var.glue_job_names :
      ["Glue", "glue.driver.aggregate.numFailedTasks", "JobName", j, "JobRunId", "ALL", "Type", "count",
    { stat = "Sum", yAxis = "right", label = "${j} failed tasks" }]],
    [["AWS/Events", "TriggeredRules", "RuleName", aws_cloudwatch_event_rule.glue_job_failed.name,
    { stat = "Sum", yAxis = "right", label = "Glue failure events" }]],
  )

  business_metrics = [
    [local.ns, "ChurnAlertVolume", "EndpointName", local.ep, { stat = "Maximum", label = "Churn alerts per day" }],
    [local.ns, "ChurnAlertVolumeChangePct", "EndpointName", local.ep,
    { stat = "Maximum", yAxis = "right", label = "Change vs prior 7-day mean (%)" }],
  ]

  layer_widgets = [
    { title = "1. Infrastructure: ${local.var} instance utilization (%)", metrics = local.infra_metrics, period = 300, stacked = false },
    { title = "2. Model serving: ${local.var} latency and request outcomes", metrics = local.serving_metrics, period = 300, stacked = false },
    { title = "3. Model quality: drift result and per-feature PSI / KS p-value", metrics = local.quality_metrics, period = 300, stacked = false },
    { title = "4. Data pipeline: Glue job runs and failure events", metrics = local.pipeline_metrics, period = 300, stacked = false },
    { title = "5. Business: daily churn-alert volume", metrics = local.business_metrics, period = 86400, stacked = false },
  ]
}

resource "aws_cloudwatch_dashboard" "platform" {
  dashboard_name = "NorthStar-AI-Platform"

  dashboard_body = jsonencode({
    widgets = concat(
      [for i, w in local.layer_widgets : {
        type   = "metric"
        x      = (i % 2) * 12
        y      = floor(i / 2) * 7
        width  = 12
        height = 7
        properties = {
          title   = w.title
          region  = var.region
          view    = "timeSeries"
          stacked = w.stacked
          period  = w.period
          metrics = w.metrics
        }
      }],
      [{
        type   = "alarm"
        x      = 12
        y      = 14
        width  = 12
        height = 7
        properties = {
          title = "Alarm status (P1 to P4; Glue failures are EventBridge events, see widget 4)"
          alarms = [
            aws_cloudwatch_metric_alarm.error_rate_high.arn,
            aws_cloudwatch_composite_alarm.serving_p2.arn,
            aws_cloudwatch_metric_alarm.latency_p95_critical.arn,
            aws_cloudwatch_metric_alarm.alert_volume_drop.arn,
            aws_cloudwatch_composite_alarm.serving_p3.arn,
            aws_cloudwatch_metric_alarm.latency_p95_high.arn,
            aws_cloudwatch_metric_alarm.cpu_p90_high.arn,
            aws_cloudwatch_metric_alarm.drift_detected.arn,
            aws_cloudwatch_metric_alarm.rollback_latency.arn,
            aws_cloudwatch_composite_alarm.serving_suppressor.arn,
          ]
        }
      }],
    )
  })
}
