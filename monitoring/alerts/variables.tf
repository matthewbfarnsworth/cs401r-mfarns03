variable "region" {
  description = "AWS region the endpoint and Glue jobs run in"
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix; every alarm and rule name contains it"
  type        = string
  default     = "northstar"
}

variable "endpoint_name" {
  description = "SageMaker real-time endpoint being monitored"
  type        = string
  default     = "northstar-churn-prod"
}

variable "monitored_variant" {
  description = "Production variant the infrastructure and serving alarms watch"
  type        = string
  default     = "champion"
}

variable "glue_job_names" {
  description = "Glue jobs whose FAILED, ERROR or TIMEOUT runs page the on-call"
  type        = list(string)
  default     = ["northstar-dev-transform", "northstar-dev-feature-engineer"]
}

variable "alert_email" {
  description = "Address subscribed to the alert topic. Set it in terraform.tfvars (gitignored), never in a committed file."
  type        = string
}

variable "metric_namespace" {
  description = "Custom namespace monitoring/publish_metrics.py writes to"
  type        = string
  default     = "NorthStar/ChurnModel"
}

variable "drift_features" {
  description = "Features in DRIFT_PLAN, by the per-feature metric publish_metrics.py writes for them"
  type        = map(list(string))
  default = {
    DriftPSI      = ["days_since_last_purchase", "purchase_frequency_30d", "avg_order_value"]
    DriftKSPValue = ["category_diversity_score", "total_spend_90d"]
  }
}
