variable "project" {
  description = "Project name, used as the first element of every resource name"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "bucket_name" {
  description = "Name of the S3 data bucket"
  type        = string
}

variable "data_engineer_role_arn" {
  description = "ARN of the DataEngineer execution role used by Feature Store"
  type        = string
}

variable "offline_store_prefix" {
  description = "S3 prefix used by the Feature Store offline store"
  type        = string
  default     = "features/offline-store/"
}
