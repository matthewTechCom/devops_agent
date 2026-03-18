variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "ap-northeast-1"
}

variable "project_name" {
  description = "Project name used in naming."
  type        = string
  default     = "devops-agent"
}

variable "environment" {
  description = "Environment name used in naming."
  type        = string
  default     = "prod"
}

variable "runtime_image_tag" {
  description = "Container image tag to deploy into AgentCore Runtime."
  type        = string
  default     = "latest"
}

variable "python_executable" {
  description = "Python executable used by Terraform local-exec / external scripts for AgentCore Identity automation."
  type        = string
  default     = "../.venv/bin/python"
}

variable "target_app_name" {
  description = "Logical application name to observe. Used to derive a default log group when default_log_group_name is unset."
  type        = string
  default     = null
}

variable "target_app_environment" {
  description = "Target application environment name. Used to derive a default log group when default_log_group_name is unset."
  type        = string
  default     = null
}

variable "target_app_component" {
  description = "Target application component used in the derived log group name."
  type        = string
  default     = "backend"
}

variable "default_log_group_name" {
  description = "Default single log group this runtime should be allowed to query."
  type        = string
  default     = null
}

variable "runtime_idle_timeout_seconds" {
  description = "Idle timeout for AgentCore Runtime sessions."
  type        = number
  default     = 900
}

variable "runtime_max_lifetime_seconds" {
  description = "Maximum lifetime for an AgentCore Runtime session."
  type        = number
  default     = 14400
}

variable "query_timeout_seconds" {
  description = "Timeout used by the MCP server while polling GetQueryResults."
  type        = number
  default     = 45
}

variable "query_poll_seconds" {
  description = "Polling interval used by the MCP server while waiting for CloudWatch query completion."
  type        = number
  default     = 1.5
}

variable "cognito_scope_name" {
  description = "OAuth scope name used by Gateway to access the Runtime via Cognito."
  type        = string
  default     = "invoke"
}

variable "cognito_gateway_client_name" {
  description = "Name of the Cognito app client used by AgentCore Gateway for client credentials."
  type        = string
  default     = "agentcore-gateway-client"
}

variable "allowed_log_group_names" {
  description = "Exact CloudWatch Logs log groups this runtime may query. If omitted, Terraform uses default_log_group_name or derives one from target_app_*."
  type        = list(string)
  default     = null
}

variable "common_tags" {
  description = "Additional tags applied to all Terraform-managed resources."
  type        = map(string)
  default     = {}
}

variable "validate_target_app_configuration" {
  description = "Guardrail toggle for target app configuration validation."
  type        = bool
  default     = true
}
