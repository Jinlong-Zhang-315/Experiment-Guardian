locals {
  name = "experiment-guardian-${var.environment}"

  availability_zones = slice(data.aws_availability_zones.available.names, 0, 2)
  public_cidrs       = ["10.42.0.0/24", "10.42.1.0/24"]
  private_cidrs      = ["10.42.10.0/24", "10.42.11.0/24"]

  web_base_url     = "https://${var.web_domain_name}"
  mcp_public_url   = "https://${var.web_domain_name}/mcp"
  mcp_scope_prefix = local.mcp_public_url
  mcp_scope_names = toset([
    "project.read",
    "experiment.check",
    "manifest.create",
    "submission.create",
    "submission.finalize",
    "submission.read",
    "experiment.query",
  ])
  mcp_oauth_scopes = [for name in sort(tolist(local.mcp_scope_names)) : "${local.mcp_scope_prefix}/${name}"]

  common_environment = concat([
    { name = "APP_ENV", value = "production" },
    { name = "DEPLOYMENT_MODE", value = "cloud" },
    { name = "LOG_LEVEL", value = "INFO" },
    { name = "WEB_AUTH_MODE", value = "cognito" },
    { name = "OBJECT_STORAGE_BACKEND", value = "aws_s3" },
    { name = "QUEUE_BACKEND", value = "sqs" },
    { name = "LLM_PROVIDER", value = "bedrock" },
    { name = "AWS_REGION", value = var.aws_region },
    { name = "S3_BUCKET", value = aws_s3_bucket.artifacts.id },
    { name = "SQS_SUBMISSION_QUEUE_URL", value = aws_sqs_queue.submissions.url },
    { name = "BEDROCK_SUMMARY_MODEL_ID", value = var.bedrock_summary_model_id },
    { name = "BEDROCK_EMBEDDING_MODEL_ID", value = var.bedrock_embedding_model_id },
    { name = "EMBEDDING_DIMENSION", value = "1024" },
    { name = "WEB_PUBLIC_BASE_URL", value = local.web_base_url },
    { name = "WEB_FRONTEND_URL", value = local.web_base_url },
    { name = "COGNITO_ISSUER_URL", value = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}" },
    { name = "COGNITO_DOMAIN", value = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com" },
    { name = "COGNITO_WEB_CLIENT_ID", value = aws_cognito_user_pool_client.web.id },
    { name = "AGENT_ENABLED", value = tostring(var.agent_enabled) },
    ], var.agent_enabled ? [
    { name = "AGENT_PROVIDER", value = "bailian" },
    { name = "BAILIAN_BASE_URL", value = var.bailian_agent_base_url },
    { name = "BAILIAN_AGENT_MODEL", value = var.bailian_agent_model },
  ] : [])

  common_secrets = concat([
    { name = "DATABASE_URL", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:database_url::" },
    { name = "WEB_OIDC_STATE_KEY", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:web_oidc_state_key::" },
    { name = "WEB_CSRF_SECRET", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:web_csrf_secret::" },
    { name = "COGNITO_WEB_CLIENT_SECRET", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:cognito_web_client_secret::" },
    ], var.agent_enabled ? [
    { name = "BAILIAN_API_KEY", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:bailian_agent_api_key::" },
  ] : [])
}

data "aws_availability_zones" "available" {
  state = "available"
}
