resource "random_string" "suffix" {
  length  = 6
  lower   = true
  upper   = false
  numeric = true
  special = false
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  derived_default_log_group_name = (
    var.target_app_name != null && var.target_app_environment != null
  ) ? "/ecs/${var.target_app_name}-${var.target_app_environment}-${var.target_app_component}" : null
  effective_default_log_group_name = var.default_log_group_name != null ? var.default_log_group_name : local.derived_default_log_group_name
  effective_allowed_log_group_names = var.allowed_log_group_names != null ? var.allowed_log_group_names : (
    local.effective_default_log_group_name != null ? [local.effective_default_log_group_name] : []
  )
  effective_allowed_log_group_names_csv = join(",", local.effective_allowed_log_group_names)

  base_name = replace(
    lower("${var.project_name}-${var.environment}-${random_string.suffix.result}"),
    "/[^0-9a-z-]/",
    "-",
  )

  runtime_name = trimsuffix(
    substr(
      "CwInsights_${replace(lower("${var.project_name}_${var.environment}_${random_string.suffix.result}"), "/[^0-9a-z_]/", "_")}",
      0,
      48,
    ),
    "_",
  )

  gateway_name          = trimsuffix(substr("${local.base_name}-gateway", 0, 100), "-")
  target_name           = "cwlogs"
  cognito_domain_prefix = trimsuffix(substr(replace("${local.base_name}-auth", "/[^0-9a-z-]/", "-"), 0, 63), "-")
  oauth_provider_name   = trimsuffix(substr(replace("${local.base_name}-cognito", "/[^0-9A-Za-z_-]/", "-"), 0, 128), "-")

  runtime_repository_name    = trimsuffix(substr("${local.base_name}-runtime", 0, 256), "-")
  runtime_image_uri          = "${aws_ecr_repository.runtime.repository_url}@${data.external.runtime_image.result["image_digest"]}"
  runtime_config_secret_name = trimsuffix(substr("${local.base_name}-runtime-config", 0, 512), "-")
  runtime_config_secret_payload = {
    TARGET_APP_NAME         = var.target_app_name != null ? var.target_app_name : ""
    TARGET_APP_ENV          = var.target_app_environment != null ? var.target_app_environment : ""
    TARGET_APP_COMPONENT    = var.target_app_component
    DEFAULT_LOG_GROUP_NAME  = local.effective_default_log_group_name != null ? local.effective_default_log_group_name : ""
    ALLOWED_LOG_GROUP_NAMES = local.effective_allowed_log_group_names_csv
  }

  runtime_log_group_arn          = "arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"
  runtime_log_stream_arn         = "arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
  allowed_log_group_arns         = flatten([for name in local.effective_allowed_log_group_names : ["arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:${name}", "arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:${name}:*"]])
  runtime_mcp_invoke_url         = "https://bedrock-agentcore.${var.aws_region}.amazonaws.com/runtimes/${urlencode(aws_cloudformation_stack.runtime.outputs["RuntimeArn"])}/invocations?qualifier=DEFAULT"
  cognito_scope_identifier       = aws_cognito_resource_server.runtime.identifier
  cognito_scope_value            = "${local.cognito_scope_identifier}/${var.cognito_scope_name}"
  cognito_issuer                 = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.runtime.id}"
  cognito_discovery_url          = "${local.cognito_issuer}/.well-known/openid-configuration"
  cognito_domain_fqdn            = "${aws_cognito_user_pool_domain.runtime.domain}.auth.${var.aws_region}.amazoncognito.com"
  cognito_authorization_endpoint = "https://${local.cognito_domain_fqdn}/oauth2/authorize"
  cognito_token_endpoint         = "https://${local.cognito_domain_fqdn}/oauth2/token"
  runtime_stack_name             = trimsuffix(substr("${local.base_name}-runtime-stack", 0, 128), "-")
  gateway_stack_name             = trimsuffix(substr("${local.base_name}-gateway-stack", 0, 128), "-")
  runtime_stack_description      = "AgentCore Runtime for CloudWatch Logs Insights MCP server."
  gateway_stack_description      = "AgentCore Gateway and MCP target for the CloudWatch Logs Insights runtime."
  gateway_instructions           = "Use query_cloudwatch_insights to run CloudWatch Logs Insights against approved log groups. Provide log_group_name, minutes, and query."

  # ------------------------------------------------------------------
  # RDS MCP Server locals
  # ------------------------------------------------------------------
  rds_target_name = "rdsquery"
  rds_runtime_name = trimsuffix(
    substr(
      "RdsQuery_${replace(lower("${var.project_name}_${var.environment}_${random_string.suffix.result}"), "/[^0-9a-z_]/", "_")}",
      0,
      48,
    ),
    "_",
  )
  rds_runtime_repository_name    = trimsuffix(substr("${local.base_name}-rds-runtime", 0, 256), "-")
  rds_runtime_image_uri          = "${aws_ecr_repository.rds_runtime.repository_url}:${var.rds_runtime_image_tag}"
  rds_runtime_config_secret_name = trimsuffix(substr("${local.base_name}-rds-runtime-config", 0, 512), "-")
  rds_runtime_config_secret_payload = {
    RDS_LAMBDA_FUNCTION_NAME = aws_lambda_function.rds_query_proxy.function_name
    QUERY_MAX_ROWS           = tostring(var.rds_max_rows)
  }
  rds_runtime_stack_name        = trimsuffix(substr("${local.base_name}-rds-runtime-stack", 0, 128), "-")
  rds_runtime_stack_description = "AgentCore Runtime for RDS Query MCP server."
  rds_gateway_target_stack_name = trimsuffix(substr("${local.base_name}-rds-gw-target-stack", 0, 128), "-")
  rds_runtime_mcp_invoke_url    = "https://bedrock-agentcore.${var.aws_region}.amazonaws.com/runtimes/${urlencode(aws_cloudformation_stack.rds_runtime.outputs["RuntimeArn"])}/invocations?qualifier=DEFAULT"

  # ------------------------------------------------------------------
  # GitHub Actions MCP Server locals
  # ------------------------------------------------------------------
  gha_target_name = "ghactions"
  gha_runtime_name = trimsuffix(
    substr(
      "GhActions_${replace(lower("${var.project_name}_${var.environment}_${random_string.suffix.result}"), "/[^0-9a-z_]/", "_")}",
      0,
      48,
    ),
    "_",
  )
  gha_runtime_repository_name    = trimsuffix(substr("${local.base_name}-gha-runtime", 0, 256), "-")
  gha_runtime_image_uri          = "${aws_ecr_repository.gha_runtime.repository_url}:${var.gha_runtime_image_tag}"
  gha_runtime_config_secret_name = trimsuffix(substr("${local.base_name}-gha-runtime-config", 0, 512), "-")
  gha_runtime_config_secret_payload = {
    GITHUB_REPOSITORY    = var.github_repository
    ALLOWED_REPOSITORIES = join(",", local.gha_effective_allowed_repositories)
  }
  gha_effective_allowed_repositories = var.gha_allowed_repositories != null ? var.gha_allowed_repositories : (
    var.github_repository != "" ? [var.github_repository] : []
  )
  gha_runtime_stack_name        = trimsuffix(substr("${local.base_name}-gha-runtime-stack", 0, 128), "-")
  gha_runtime_stack_description = "AgentCore Runtime for GitHub Actions MCP server."
  gha_gateway_target_stack_name = trimsuffix(substr("${local.base_name}-gha-gw-target-stack", 0, 128), "-")
  gha_runtime_mcp_invoke_url    = "https://bedrock-agentcore.${var.aws_region}.amazonaws.com/runtimes/${urlencode(aws_cloudformation_stack.gha_runtime.outputs["RuntimeArn"])}/invocations?qualifier=DEFAULT"

  # ------------------------------------------------------------------
  # Orchestrator MCP Server locals
  # ------------------------------------------------------------------
  orchestrator_target_name = "orchestrator"
  orchestrator_runtime_name = trimsuffix(
    substr(
      "Orchestr_${replace(lower("${var.project_name}_${var.environment}_${random_string.suffix.result}"), "/[^0-9a-z_]/", "_")}",
      0,
      48,
    ),
    "_",
  )
  orchestrator_runtime_repository_name    = trimsuffix(substr("${local.base_name}-orch-runtime", 0, 256), "-")
  orchestrator_runtime_image_uri          = "${aws_ecr_repository.orchestrator_runtime.repository_url}:${var.orchestrator_runtime_image_tag}"
  orchestrator_runtime_config_secret_name = trimsuffix(substr("${local.base_name}-orch-runtime-config", 0, 512), "-")
  orchestrator_runtime_config_secret_payload = {
    BEDROCK_MODEL_ID   = var.orchestrator_bedrock_model_id
    MAX_REACT_STEPS    = tostring(var.orchestrator_max_react_steps)
    BEDROCK_MAX_TOKENS = tostring(var.orchestrator_bedrock_max_tokens)
  }
  orchestrator_runtime_stack_name        = trimsuffix(substr("${local.base_name}-orch-runtime-stack", 0, 128), "-")
  orchestrator_runtime_stack_description = "AgentCore Runtime for Orchestrator MCP server."
  orchestrator_gateway_target_stack_name = trimsuffix(substr("${local.base_name}-orch-gw-target-stack", 0, 128), "-")
  orchestrator_runtime_mcp_invoke_url    = "https://bedrock-agentcore.${var.aws_region}.amazonaws.com/runtimes/${urlencode(aws_cloudformation_stack.orchestrator_runtime.outputs["RuntimeArn"])}/invocations?qualifier=DEFAULT"

  tags = merge(var.common_tags, {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
  })
}
