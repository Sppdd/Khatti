terraform {
  required_version = ">= 1.6"

  required_providers {
    nebius = {
      # Nebius serves its provider from its own registry mirror.
      source = "terraform-provider.storage.eu-north1.nebius.cloud/nebius/nebius"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
  }

  # State in Object Storage (S3 API). Create the state bucket once by hand, then:
  #   terraform init -backend-config=backend.hcl
  backend "s3" {
    key                         = "khatti/terraform.tfstate"
    region                      = "eu-north1"
    endpoints                   = { s3 = "https://storage.eu-north1.nebius.cloud" }
    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
    use_path_style              = true
  }
}

provider "nebius" {
  # Auth: `nebius` CLI profile or a service-account credentials file (see README).
}
