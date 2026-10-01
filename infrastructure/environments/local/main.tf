# ── environments/local ───────────────────────────────────────────────────────
# Calls the same modules as environments/dev. The sagemaker module is omitted
# on purpose: SageMaker is not in LocalStack Community.
#
# These calls are live, not commented out, so `make local-validate` works the
# moment your vpc, storage, and iam modules are implemented. Until then,
# terraform validate still passes — an empty module is a valid module.

module "vpc" {
  source             = "../../modules/vpc"
  project            = var.project
  environment        = var.environment
  enable_nat_gateway = false
}

module "storage" {
  source                 = "../../modules/storage"
  project                = var.project
  environment            = var.environment
  enable_lifecycle_rules = false
  force_destroy          = true
}

module "iam" {
  source      = "../../modules/iam"
  project     = var.project
  environment = var.environment
}
