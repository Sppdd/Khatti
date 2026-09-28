# Khatti on Nebius AI Cloud (eu-north1): Object Storage, Managed PostgreSQL, Container
# Registry, Managed MLflow and service accounts.
#
# Attribute names and types were checked against the Nebius API schemas in the official
# `nebius` Python SDK (0.6.14), which the provider is generated from (spec fields sit at the
# resource top level). `terraform validate` against the real provider is still required: the
# provider registry was unreachable from the build environment. Serverless Endpoints and Jobs
# are deployed with deploy/nebius_deploy.py (typed SDK, ai.v1).

locals {
  name = "khatti-${var.env}"
}

# ---------------------------------------------------------------- Object Storage

resource "nebius_storage_v1_bucket" "images" {
  parent_id         = var.project_id
  name              = "${local.name}-images"
  versioning_policy = "DISABLED"
  # Lifecycle: stray plaintext uploads are deleted after 1 day; the app itself deletes
  # KYC originals N days after the final decision (KHATTI_RETENTION_DAYS).
  lifecycle_configuration = {
    rules = [{
      id         = "expire-uploads"
      status     = "ENABLED"
      filter     = { prefix = "uploads/" }
      expiration = { days = 1 }
    }]
  }
}

resource "nebius_storage_v1_bucket" "artifacts" {
  parent_id         = var.project_id
  name              = "${local.name}-artifacts" # synthetic data, eval reports, calibrators
  versioning_policy = "ENABLED"
}

# ---------------------------------------------------------------- service accounts

resource "nebius_iam_v1_service_account" "app" {
  parent_id = var.project_id
  name      = "${local.name}-app"
}

resource "nebius_iam_v1_service_account" "jobs" {
  parent_id = var.project_id
  name      = "${local.name}-jobs"
}

# S3 access keys (IAM v2). The secret goes to MysteryBox, as the API recommends for Terraform;
# read it from there when configuring the app (AWS_SECRET_ACCESS_KEY).
resource "nebius_iam_v2_access_key" "app" {
  parent_id = var.project_id
  account = {
    service_account = { id = nebius_iam_v1_service_account.app.id }
  }
  secret_delivery_mode = "MYSTERY_BOX"
}

resource "nebius_iam_v2_access_key" "jobs" {
  parent_id = var.project_id
  account = {
    service_account = { id = nebius_iam_v1_service_account.jobs.id }
  }
  secret_delivery_mode = "MYSTERY_BOX"
}

# ---------------------------------------------------------------- Container Registry

resource "nebius_registry_v1_registry" "main" {
  parent_id = var.project_id
  name      = local.name
}

# ---------------------------------------------------------------- Managed PostgreSQL

resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "nebius_msp_postgresql_v1alpha1_cluster" "main" {
  parent_id  = var.project_id
  name       = local.name
  network_id = data.nebius_vpc_v1_subnet.main.network_id
  config = {
    version = "16"
    template = {
      resources = { platform = "cpu-e2", preset = var.postgres_preset }
      hosts     = { count = 1 }
      disk      = { type = "network-ssd", size_gibibytes = var.postgres_disk_gib }
    }
  }
  bootstrap = {
    db_name       = "khatti"
    user_name     = "khatti_app" # not a superuser: Row-Level Security applies
    user_password = random_password.postgres.result
  }
}

data "nebius_vpc_v1_subnet" "main" {
  id = var.subnet_id
}

# ---------------------------------------------------------------- Managed MLflow

resource "random_password" "mlflow" {
  length  = 24
  special = false
}

resource "nebius_msp_mlflow_v1alpha1_cluster" "main" {
  parent_id           = var.project_id
  name                = local.name
  network_id          = data.nebius_vpc_v1_subnet.main.network_id
  admin_username      = var.mlflow_admin_user
  admin_password      = random_password.mlflow.result
  service_account_id  = nebius_iam_v1_service_account.jobs.id
  public_access       = true
  storage_bucket_name = nebius_storage_v1_bucket.artifacts.name
}

# ---------------------------------------------------------------- IAM

resource "nebius_iam_v1_group_membership" "app" {
  parent_id = var.editors_group_id
  member_id = nebius_iam_v1_service_account.app.id
}

resource "nebius_iam_v1_group_membership" "jobs" {
  parent_id = var.editors_group_id
  member_id = nebius_iam_v1_service_account.jobs.id
}
