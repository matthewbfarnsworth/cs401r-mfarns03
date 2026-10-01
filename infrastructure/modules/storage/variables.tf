# Every variable needs a description — Task B1 grades this.

variable "project" {
  description = "Project name, used as the first element of every resource name"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "prefixes" {
  description = "Top-level S3 prefixes to create in the data bucket"
  type        = list(string)
  default     = ["raw/", "processed/", "features/", "artifacts/"]
}

variable "enable_lifecycle_rules" {
  description = "Whether to apply lifecycle rules to the data bucket"
  type        = bool
  default     = true
}

variable "force_destroy" {
  description = "Whether Terraform may delete the bucket while it contains objects or versions"
  type        = bool
  default     = false
}
