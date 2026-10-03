output "database_name" {
  description = "Name of the Glue Catalog database"
  value       = aws_glue_catalog_database.this.name
}

output "network_connection_name" {
  description = "Name of the private Glue network connection"
  value       = aws_glue_connection.network.name
}

output "raw_crawler_name" {
  description = "Name of the raw-data Glue crawler"
  value       = aws_glue_crawler.raw.name
}

output "transform_job_name" {
  description = "Name of the transform Glue job"
  value       = aws_glue_job.transform.name
}

output "transform_script_s3_uri" {
  description = "S3 URI of the Terraform-managed transform script"
  value       = "s3://${var.bucket_name}/${aws_s3_object.transform_script.key}"
}
