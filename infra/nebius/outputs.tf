output "images_bucket" {
  value = nebius_storage_v1_bucket.images.name
}

output "artifacts_bucket" {
  value = nebius_storage_v1_bucket.artifacts.name
}

output "registry_id" {
  value = nebius_registry_v1_registry.main.id
}

output "postgres_cluster_id" {
  value = nebius_msp_postgresql_v1alpha1_cluster.main.id
}

output "postgres_password" {
  value     = random_password.postgres.result
  sensitive = true
}

output "mlflow_cluster_id" {
  value = nebius_msp_mlflow_v1alpha1_cluster.main.id
}

output "app_aws_access_key_id" {
  value = nebius_iam_v2_access_key.app.status.aws_access_key_id
}

output "app_access_key_secret_reference" {
  description = "MysteryBox secret holding AWS_SECRET_ACCESS_KEY for the app."
  value       = nebius_iam_v2_access_key.app.status.secret_reference_id
}

output "jobs_aws_access_key_id" {
  value = nebius_iam_v2_access_key.jobs.status.aws_access_key_id
}
