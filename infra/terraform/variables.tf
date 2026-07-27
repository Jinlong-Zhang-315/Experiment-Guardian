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

variable "agent_enabled" {
  type        = bool
  default     = false
  description = "Create the optional R15a governance Agent worker and expose the Web feature."
}

variable "agent_provider" {
  type        = string
  default     = "bailian"
  description = "Governance Agent provider: bailian or bedrock. There is no runtime fallback."
  validation {
    condition     = contains(["bailian", "bedrock"], var.agent_provider)
    error_message = "agent_provider must be bailian or bedrock."
  }
}

variable "bailian_agent_base_url" {
  type        = string
  default     = ""
  description = "Alibaba Cloud Model Studio OpenAI-compatible base URL."
}

variable "bailian_agent_model" {
  type        = string
  default     = ""
  description = "Function-calling model used by the internal governance Agent."
}

variable "bailian_agent_api_key" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Alibaba Cloud Model Studio API key; stored in the encrypted runtime secret."
}

variable "bedrock_agent_model_id" {
  type        = string
  default     = ""
  description = "Bedrock Converse model ID used when agent_provider is bedrock."
}

variable "agent_cost_currency" {
  type        = string
  default     = "USD"
  description = "ISO-like three-letter label for configured Agent rate estimates."
  validation {
    condition     = can(regex("^[A-Z]{3}$", var.agent_cost_currency))
    error_message = "agent_cost_currency must contain exactly three uppercase letters."
  }
}

variable "agent_input_cost_per_million_tokens" {
  type        = number
  default     = null
  description = "Optional configured input-token rate per million tokens; not a billing feed."
  validation {
    condition     = var.agent_input_cost_per_million_tokens == null || var.agent_input_cost_per_million_tokens >= 0
    error_message = "agent_input_cost_per_million_tokens must be non-negative."
  }
}

variable "agent_output_cost_per_million_tokens" {
  type        = number
  default     = null
  description = "Optional configured output-token rate per million tokens; not a billing feed."
  validation {
    condition     = var.agent_output_cost_per_million_tokens == null || var.agent_output_cost_per_million_tokens >= 0
    error_message = "agent_output_cost_per_million_tokens must be non-negative."
  }
}
