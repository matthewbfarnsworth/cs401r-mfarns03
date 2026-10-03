output "feature_group_name" {
  description = "Name of the SageMaker customer Feature Group"
  value       = aws_sagemaker_feature_group.customer_features.feature_group_name
}

output "feature_group_arn" {
  description = "ARN of the SageMaker customer Feature Group"
  value       = aws_sagemaker_feature_group.customer_features.arn
}

output "offline_store_s3_uri" {
  description = "S3 URI used by the Feature Store offline store"
  value       = "s3://${var.bucket_name}/${var.offline_store_prefix}"
}
