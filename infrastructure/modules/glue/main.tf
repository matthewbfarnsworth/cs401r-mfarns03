locals {
  name_prefix   = "${var.project}-${var.environment}"
  database_name = lower(replace("${var.project}_${var.environment}", "-", "_"))
}

resource "aws_glue_catalog_database" "this" {
  name        = local.database_name
  description = "NorthStar ${var.environment} data catalog"

  tags = {
    Name = local.database_name
  }
}

resource "aws_glue_connection" "network" {
  name            = "${local.name_prefix}-network"
  description     = "Private network connection for NorthStar Glue jobs"
  connection_type = "NETWORK"

  physical_connection_requirements {
    availability_zone      = var.availability_zone
    security_group_id_list = [var.security_group_id]
    subnet_id              = var.private_subnet_id
  }

  tags = {
    Name = "${local.name_prefix}-network"
  }
}

resource "aws_glue_crawler" "raw" {
  name          = "${local.name_prefix}-raw-crawler"
  description   = "Discovers the raw customer transaction schema"
  database_name = aws_glue_catalog_database.this.name
  role          = var.data_engineer_role_arn

  s3_target {
    path = "s3://${var.bucket_name}/${var.raw_data_prefix}"
  }

  schema_change_policy {
    delete_behavior = "LOG"
    update_behavior = "UPDATE_IN_DATABASE"
  }

  tags = {
    Name = "${local.name_prefix}-raw-crawler"
  }
}

resource "aws_s3_object" "transform_script" {
  bucket       = var.bucket_name
  key          = var.transform_script_key
  source       = var.transform_script_path
  etag         = filemd5(var.transform_script_path)
  content_type = "text/x-python"
}

resource "aws_s3_object" "feature_engineer_script" {
  bucket       = var.bucket_name
  key          = var.feature_engineer_script_key
  source       = var.feature_engineer_script_path
  etag         = filemd5(var.feature_engineer_script_path)
  content_type = "text/x-python"
}

resource "aws_glue_job" "transform" {
  name              = "${local.name_prefix}-transform"
  description       = "Cleans raw customer transactions and writes processed Parquet"
  role_arn          = var.data_engineer_role_arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  max_retries       = 0
  timeout           = 30
  connections       = [aws_glue_connection.network.name]

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${var.bucket_name}/${aws_s3_object.transform_script.key}"
  }

  default_arguments = {
    "--job-language"                     = "python"
    "--enable-glue-datacatalog"          = "true"
    "--enable-metrics"                   = ""
    "--enable-continuous-cloudwatch-log" = "true"
    "--database_name"                    = aws_glue_catalog_database.this.name
    "--table_name"                       = var.raw_table_name
    "--output_path"                      = "s3://${var.bucket_name}/${var.processed_data_prefix}"
    "--TempDir"                          = "s3://${var.bucket_name}/processed/glue-temp/"
  }

  execution_property {
    max_concurrent_runs = 1
  }

  tags = {
    Name = "${local.name_prefix}-transform"
  }
}

resource "aws_glue_job" "feature_engineer" {
  name              = "${local.name_prefix}-feature-engineer"
  description       = "Computes customer features and ingests them into Feature Store"
  role_arn          = var.data_engineer_role_arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  max_retries       = 0
  timeout           = 60
  connections       = [aws_glue_connection.network.name]

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${var.bucket_name}/${aws_s3_object.feature_engineer_script.key}"
  }

  default_arguments = {
    "--job-language"                     = "python"
    "--enable-glue-datacatalog"          = "true"
    "--enable-metrics"                   = ""
    "--enable-continuous-cloudwatch-log" = "true"
    "--input_path"                       = "s3://${var.bucket_name}/${var.processed_data_prefix}"
    "--output_path"                      = "s3://${var.bucket_name}/${var.feature_data_prefix}"
    "--feature_group_name"               = var.feature_group_name
    "--region"                           = var.aws_region
    "--TempDir"                          = "s3://${var.bucket_name}/features/glue-temp/"
  }

  execution_property {
    max_concurrent_runs = 1
  }

  tags = {
    Name = "${local.name_prefix}-feature-engineer"
  }
}
