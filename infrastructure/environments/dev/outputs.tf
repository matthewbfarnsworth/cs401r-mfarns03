# Surface what later labs need. Lab 2 reads these from `terraform output`,
# and scripts/verify-lab1.sh reads ALL FIVE of them with `terraform output
# -raw`, so every one must exist (uncommented and wired) before you run it.

output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "public_subnet_id" {
  description = "ID of the public subnet"
  value       = module.vpc.public_subnet_id
}

output "private_subnet_id" {
  description = "ID of the private subnet"
  value       = module.vpc.private_subnet_id
}

output "s3_bucket_name" {
  description = "Name of the data bucket"
  value       = module.storage.bucket_name
}

output "ml_engineer_role_arn" {
  description = "ARN of the MLEngineer role"
  value       = module.iam.ml_engineer_role_arn
}

output "data_engineer_role_arn" {
  description = "ARN of the DataEngineer role"
  value       = module.iam.data_engineer_role_arn
}

output "model_monitor_role_arn" {
  description = "ARN of the ModelMonitor role"
  value       = module.iam.model_monitor_role_arn
}

output "glue_database_name" {
  description = "Name of the Glue Catalog database"
  value       = module.glue.database_name
}

output "raw_crawler_name" {
  description = "Name of the raw-data Glue crawler"
  value       = module.glue.raw_crawler_name
}

output "transform_job_name" {
  description = "Name of the transform Glue job"
  value       = module.glue.transform_job_name
}

output "sagemaker_domain_id" {
  description = "ID of the SageMaker Domain"
  value       = module.sagemaker.domain_id
}
