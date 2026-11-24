output "alerts_topic_arn" {
  description = "SNS topic every NorthStar alert publishes to"
  value       = aws_sns_topic.alerts.arn
}

output "glue_failure_rule" {
  description = "EventBridge rule for failed Glue job runs"
  value       = aws_cloudwatch_event_rule.glue_job_failed.name
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.platform.dashboard_name
}
