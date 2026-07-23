resource "aws_kms_key" "main" {
  description             = "Experiment Guardian ${var.environment} data"
  deletion_window_in_days = 14
  enable_key_rotation     = true
}

resource "aws_kms_alias" "main" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.main.key_id
}

resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${local.name}-artifacts-"
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.main.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "retain-auditable-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "STANDARD_IA"
    }
  }
}

resource "aws_s3_bucket" "web" {
  bucket_prefix = "${local.name}-web-"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_sqs_queue" "dead_letter" {
  name                      = "${local.name}-submission-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = aws_kms_key.main.arn
}

resource "aws_sqs_queue" "submissions" {
  name                       = "${local.name}-submissions"
  visibility_timeout_seconds = 120
  receive_wait_time_seconds  = 20
  message_retention_seconds  = 345600
  kms_master_key_id          = aws_kms_key.main.arn
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = 5
  })
}

resource "random_password" "oidc_state_key" {
  length  = 48
  special = false
}

resource "random_password" "csrf_secret" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "runtime" {
  name                    = "${local.name}/runtime"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = 14
}

resource "aws_secretsmanager_secret_version" "runtime" {
  secret_id = aws_secretsmanager_secret.runtime.id
  secret_string = jsonencode({
    database_url              = var.cockroach_database_url
    web_oidc_state_key        = random_password.oidc_state_key.result
    web_csrf_secret           = random_password.csrf_secret.result
    cognito_web_client_secret = aws_cognito_user_pool_client.web.client_secret
    bailian_agent_api_key     = var.bailian_agent_api_key
  })
}
