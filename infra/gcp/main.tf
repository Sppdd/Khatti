# Portability proof (plan O): the same images on Google Cloud. Documented, not deployed for
# the hackathon. Khatti only needs an S3-compatible bucket (GCS with HMAC keys), Postgres 16
# and two containers, so the app code does not change.

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

variable "project" { type = string }
variable "region" {
  type    = string
  default = "me-central1" # Doha
}
variable "api_image" { type = string }
variable "db_password" {
  type      = string
  sensitive = true
}

provider "google" {
  project = var.project
  region  = var.region
}

resource "google_storage_bucket" "images" {
  name                        = "${var.project}-khatti-images"
  location                    = var.region
  uniform_bucket_level_access = true
  lifecycle_rule {
    condition {
      age            = 1
      matches_prefix = ["uploads/"]
    }
    action { type = "Delete" }
  }
}

resource "google_sql_database_instance" "main" {
  name             = "khatti"
  database_version = "POSTGRES_16"
  region           = var.region
  settings {
    tier = "db-custom-2-8192"
  }
  deletion_protection = true
}

resource "google_sql_database" "khatti" {
  name     = "khatti"
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "app" {
  name     = "khatti_app"
  instance = google_sql_database_instance.main.name
  password = var.db_password
}

resource "google_service_account" "app" {
  account_id = "khatti-app"
}

resource "google_storage_bucket_iam_member" "app" {
  bucket = google_storage_bucket.images.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.app.email}"
}

resource "google_cloud_run_v2_service" "api" {
  name     = "khatti-api"
  location = var.region
  template {
    service_account = google_service_account.app.email
    containers {
      image = var.api_image
      ports { container_port = 8000 }
      env {
        name  = "KHATTI_S3_ENDPOINT_URL"
        value = "https://storage.googleapis.com"
      }
      env {
        name  = "KHATTI_S3_BUCKET"
        value = google_storage_bucket.images.name
      }
    }
  }
}

resource "google_cloud_run_v2_service" "worker" {
  name     = "khatti-worker"
  location = var.region
  template {
    service_account = google_service_account.app.email
    scaling {
      min_instance_count = 1
    }
    containers {
      image   = var.api_image
      command = ["python", "-m", "khatti.worker"]
    }
  }
}
