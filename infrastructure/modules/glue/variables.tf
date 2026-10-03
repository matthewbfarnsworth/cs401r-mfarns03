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
  description = "ARN of the DataEngineer execution role used by Glue"
  type        = string
}

variable "private_subnet_id" {
  description = "ID of the private subnet used by Glue job workers"
  type        = string
}

variable "security_group_id" {
  description = "ID of the self-referencing security group used by Glue job workers"
  type        = string
}

variable "availability_zone" {
  description = "Availability Zone containing the private subnet"
  type        = string
}

variable "transform_script_path" {
  description = "Local path to the transform Glue script uploaded by Terraform"
  type        = string
}

variable "feature_group_name" {
  description = "Name of the SageMaker Feature Group receiving engineered records"
  type        = string
}

variable "aws_region" {
  description = "AWS region used by the Feature Store runtime client"
  type        = string
}

variable "feature_engineer_script_path" {
  description = "Local path to the feature engineering Glue script"
  type        = string
}

variable "raw_data_prefix" {
  description = "S3 prefix containing raw customer transaction CSV files"
  type        = string
  default     = "raw/customers/"
}

variable "processed_data_prefix" {
  description = "S3 prefix where the transform job writes processed Parquet"
  type        = string
  default     = "processed/customers/"
}

variable "transform_script_key" {
  description = "S3 object key used for the transform Glue script"
  type        = string
  default     = "artifacts/glue/transform.py"
}

variable "feature_engineer_script_key" {
  description = "S3 object key used for the feature engineering Glue script"
  type        = string
  default     = "artifacts/glue/feature_engineer.py"
}

variable "feature_data_prefix" {
  description = "S3 prefix where engineered customer features are written"
  type        = string
  default     = "features/customers/"
}

variable "raw_table_name" {
  description = "Glue Catalog table name created by the raw-data crawler"
  type        = string
  default     = "customers"
}
