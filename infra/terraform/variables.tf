variable "aws_region" {
  type        = string
  description = "AWS deployment region. The CloudFront certificate must still be in us-east-1."
  default     = "us-east-1"
}

variable "environment" {
  type        = string
  description = "Short environment name."
  default     = "demo"
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "web_domain_name" {
  type        = string
  description = "Public CloudFront hostname, for example guardian.example.com."
}

variable "api_origin_domain_name" {
  type        = string
  description = "Route53 name pointed at the ALB, for example guardian-origin.example.com."
}

variable "route53_zone_id" {
  type        = string
  description = "Public Route53 hosted zone ID."
}

variable "cloudfront_certificate_arn" {
  type        = string
  description = "ACM certificate in us-east-1 for web_domain_name."
}

variable "alb_certificate_arn" {
  type        = string
  description = "Regional ACM certificate for api_origin_domain_name."
}

variable "cockroach_database_url" {
  type        = string
  sensitive   = true
  description = "Cockroach Cloud SQLAlchemy URL with sslmode=verify-full."
}

variable "bedrock_summary_model_id" {
  type        = string
  description = "Enabled Bedrock Converse model ID."
}

variable "bedrock_embedding_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}

variable "container_image_tag" {
  type    = string
  default = "latest"
}

variable "cognito_domain_prefix" {
  type        = string
  description = "Globally unique Cognito managed-login domain prefix."
}

variable "mcp_clients" {
  description = "Explicitly pre-registered public MCP clients. No dynamic registration is deployed."
  type = map(object({
    name          = string
    callback_urls = list(string)
    logout_urls   = optional(list(string), [])
  }))
  default = {}
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "mcp_desired_count" {
  type    = number
  default = 1
}
