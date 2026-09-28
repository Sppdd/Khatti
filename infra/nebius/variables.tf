variable "project_id" {
  description = "Nebius project (parent) ID in eu-north1."
  type        = string
}

variable "subnet_id" {
  description = "Existing VPC subnet ID for PostgreSQL and MLflow."
  type        = string
}

variable "env" {
  description = "Environment name, used as a prefix (e.g. dev, demo)."
  type        = string
  default     = "demo"
}

variable "postgres_preset" {
  description = "Managed PostgreSQL resource preset."
  type        = string
  default     = "2vcpu-8gb"
}

variable "postgres_disk_gib" {
  type    = number
  default = 32
}

variable "mlflow_admin_user" {
  type    = string
  default = "khatti"
}

variable "editors_group_id" {
  description = "IAM group granting the service accounts access to the project's storage and services (e.g. the project's editors group)."
  type        = string
}
