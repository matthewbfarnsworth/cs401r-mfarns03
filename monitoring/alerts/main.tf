# NorthStar monitoring: alert routing (SNS) and the pipeline-layer failure rule.
# Alarms are in alarms.tf, the dashboard in dashboard.tf.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
}

# One topic every alert publishes to. The email subscriber must click the
# confirmation link AWS sends before any notification is delivered.
resource "aws_sns_topic" "alerts" {
  name = "${var.project}-ml-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# CloudWatch alarms in the same account can publish under the default topic
# policy; EventBridge cannot, so the policy names both service principals.
data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "AccountOwner"
    actions   = ["SNS:Publish", "SNS:Subscribe", "SNS:GetTopicAttributes", "SNS:SetTopicAttributes", "SNS:ListSubscriptionsByTopic"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid       = "CloudWatchAlarms"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }

  statement {
    sid       = "EventBridgeRules"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.glue_job_failed.arn]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

# Pipeline layer. Glue publishes job metrics only while a run is executing, so
# a failed run can leave nothing to alarm on. The state-change event is the
# reliable signal: it fires once per run that ends in a failure state.
resource "aws_cloudwatch_event_rule" "glue_job_failed" {
  name        = "${var.project}-glue-job-failed"
  description = "P2 Pipeline: a NorthStar Glue job run ended FAILED, ERROR or TIMEOUT"
  tags        = { Severity = "P2", Layer = "pipeline" }

  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = var.glue_job_names
      state   = ["FAILED", "ERROR", "TIMEOUT"]
    }
  })
}

resource "aws_cloudwatch_event_target" "glue_job_failed_sns" {
  rule      = aws_cloudwatch_event_rule.glue_job_failed.name
  target_id = "alerts-topic"
  arn       = aws_sns_topic.alerts.arn

  input_transformer {
    input_paths = {
      job   = "$.detail.jobName"
      run   = "$.detail.jobRunId"
      state = "$.detail.state"
      msg   = "$.detail.message"
      time  = "$.time"
    }
    input_template = "\"P2 NorthStar pipeline: Glue job <job> run <run> ended <state> at <time>: <msg>\""
  }
}
