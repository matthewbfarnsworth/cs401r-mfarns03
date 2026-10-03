locals {
  name_prefix = "${var.project}-${var.environment}"

  feature_definitions = [
    { feature_name = "customer_id", feature_type = "String" },
    { feature_name = "event_time", feature_type = "Fractional" },
    { feature_name = "days_since_last_purchase", feature_type = "Fractional" },
    { feature_name = "customer_tenure_days", feature_type = "Fractional" },
    { feature_name = "purchase_frequency_30d", feature_type = "Fractional" },
    { feature_name = "purchase_frequency_90d", feature_type = "Fractional" },
    { feature_name = "purchase_frequency_180d", feature_type = "Fractional" },
    { feature_name = "avg_order_value", feature_type = "Fractional" },
    { feature_name = "total_spend_90d", feature_type = "Fractional" },
    { feature_name = "total_lifetime_value", feature_type = "Fractional" },
    { feature_name = "avg_basket_size_6m", feature_type = "Fractional" },
    { feature_name = "category_diversity_score", feature_type = "Fractional" },
    { feature_name = "online_to_store_ratio", feature_type = "Fractional" },
    { feature_name = "loyalty_tier", feature_type = "String" },
    { feature_name = "churn_risk_score", feature_type = "Fractional" },
    { feature_name = "churn_label", feature_type = "Integral" },
  ]
}

resource "aws_sagemaker_feature_group" "customer_features" {
  feature_group_name             = "${local.name_prefix}-customer-features"
  record_identifier_feature_name = "customer_id"
  event_time_feature_name        = "event_time"
  role_arn                       = var.data_engineer_role_arn

  dynamic "feature_definition" {
    for_each = local.feature_definitions

    content {
      feature_name = feature_definition.value.feature_name
      feature_type = feature_definition.value.feature_type
    }
  }

  online_store_config {
    enable_online_store = true
  }

  offline_store_config {
    disable_glue_table_creation = false

    s3_storage_config {
      s3_uri = "s3://${var.bucket_name}/${var.offline_store_prefix}"
    }
  }

  tags = {
    Name = "${local.name_prefix}-customer-features"
  }
}
