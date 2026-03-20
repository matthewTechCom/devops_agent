variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
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

# ------------------------------------------------------------------
# RDS MCP Server variables
# ------------------------------------------------------------------

variable "rds_runtime_image_tag" {
  description = "Container image tag for the RDS MCP Server runtime."
  type        = string
  default     = "latest"
}

variable "rds_vpc_id" {
  description = "VPC ID of the todo_sample application where the RDS instance lives."
  type        = string
  default     = null
}

variable "rds_lambda_subnet_ids" {
  description = "Subnet IDs (private app subnets in todo_sample VPC) for the RDS Lambda proxy."
  type        = list(string)
  default     = []
}

variable "rds_database_url_secret_arn" {
  description = "Secrets Manager ARN containing the PostgreSQL connection string (DATABASE_URL)."
  type        = string
  default     = ""
}

variable "rds_db_security_group_id" {
  description = "Security group ID of the RDS instance to allow Lambda ingress."
  type        = string
  default     = ""
}

variable "rds_vpce_security_group_id" {
  description = "Security group ID of the VPC endpoints in the todo_sample VPC to allow Lambda access to Secrets Manager."
  type        = string
  default     = ""
}

variable "rds_statement_timeout_ms" {
  description = "SQL statement timeout in milliseconds for the Lambda proxy."
  type        = number
  default     = 30000
}

variable "rds_max_rows" {
  description = "Maximum number of rows the Lambda proxy will return per query."
  type        = number
  default     = 1000
}

# ------------------------------------------------------------------
# GitHub Actions MCP Server variables
# ------------------------------------------------------------------

variable "gha_runtime_image_tag" {
  description = "Container image tag for the GitHub Actions MCP Server runtime."
  type        = string
  default     = "latest"
}

variable "github_pat_secret_arn" {
  description = "Secrets Manager ARN containing the GitHub Personal Access Token."
  type        = string
  default     = ""
}

variable "github_repository" {
  description = "Default GitHub repository in owner/repo format."
  type        = string
  default     = ""
}

variable "gha_allowed_repositories" {
  description = "List of allowed GitHub repositories. If null, defaults to github_repository only."
  type        = list(string)
  default     = null
}

# ------------------------------------------------------------------
# Orchestrator MCP Server variables
# ------------------------------------------------------------------

variable "orchestrator_runtime_image_tag" {
  description = "Container image tag for the Orchestrator MCP Server runtime."
  type        = string
  default     = "latest"
}

variable "orchestrator_bedrock_model_id" {
  description = "Bedrock model ID used by the orchestrator for reasoning."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-20250514-v1:0"
}

variable "orchestrator_max_react_steps" {
  description = "Maximum ReAct loop iterations for the orchestrator."
  type        = number
  default     = 10
}

variable "orchestrator_bedrock_max_tokens" {
  description = "Maximum tokens for Bedrock model responses."
  type        = number
  default     = 4096
}
