terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local state on purpose: this stack is the monitoring layer for one live
  # endpoint and is torn down with it. State files are gitignored.
}

provider "aws" {
  region = var.region
}
