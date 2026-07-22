output "application_url" {
  value = local.web_base_url
}

output "mcp_resource_url" {
  value = local.mcp_public_url
}

output "mcp_protected_resource_metadata_url" {
  value = "${local.web_base_url}/.well-known/oauth-protected-resource/mcp"
}

output "cognito_issuer_url" {
  value = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}

output "cognito_managed_login_url" {
  value = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com"
}

output "web_cognito_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "mcp_pre_registered_clients" {
  value = { for key, client in aws_cognito_user_pool_client.mcp : key => client.id }
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.id
}

output "web_bucket" {
  value = aws_s3_bucket.web.id
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.web.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "runtime_secret_arn" {
  value     = aws_secretsmanager_secret.runtime.arn
  sensitive = true
}
