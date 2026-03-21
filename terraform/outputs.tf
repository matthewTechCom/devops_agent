output "ecr_repository_url" {
  description = "ECR repository URL for the MCP runtime image."
  value       = aws_ecr_repository.runtime.repository_url
}

output "runtime_name" {
  description = "AgentCore Runtime name."
  value       = local.runtime_name
}

output "runtime_role_arn" {
  description = "IAM role ARN assumed by AgentCore Runtime."
  value       = aws_iam_role.runtime.arn
}

output "runtime_arn" {
  description = "AgentCore Runtime ARN."
  value       = aws_cloudformation_stack.runtime.outputs["RuntimeArn"]
}

output "runtime_status" {
  description = "Current AgentCore Runtime status."
  value       = aws_cloudformation_stack.runtime.outputs["RuntimeStatus"]
}

output "runtime_mcp_invoke_url" {
  description = "MCP endpoint exposed by AgentCore Runtime and used as the Gateway target."
  value       = local.runtime_mcp_invoke_url
}

output "agentcore_oauth_provider_arn" {
  description = "AgentCore Identity OAuth credential provider ARN used by the Gateway target."
  value       = data.external.agentcore_oauth_provider.result["credential_provider_arn"]
}

output "default_log_group_name" {
  description = "Default target application log group configured for this runtime."
  value       = local.effective_default_log_group_name
}

output "runtime_config_secret_arn" {
  description = "Secrets Manager ARN that stores runtime configuration loaded by the MCP server."
  value       = aws_secretsmanager_secret.runtime_config.arn
}

output "allowed_log_group_names" {
  description = "All log groups this runtime may query."
  value       = local.effective_allowed_log_group_names
}

output "gateway_role_arn" {
  description = "IAM role ARN assumed by AgentCore Gateway."
  value       = aws_iam_role.gateway.arn
}

output "gateway_identifier" {
  description = "Gateway identifier."
  value       = aws_cloudformation_stack.gateway.outputs["GatewayIdentifier"]
}

output "gateway_url" {
  description = "Public AgentCore Gateway MCP endpoint URL."
  value       = aws_cloudformation_stack.gateway.outputs["GatewayUrl"]
}

output "gateway_status" {
  description = "Current AgentCore Gateway status."
  value       = aws_cloudformation_stack.gateway.outputs["GatewayStatus"]
}

output "gateway_target_id" {
  description = "Gateway target ID."
  value       = aws_cloudformation_stack.gateway.outputs["GatewayTargetId"]
}

output "gateway_target_status" {
  description = "Gateway target status after synchronization."
  value       = aws_cloudformation_stack.gateway.outputs["GatewayTargetStatus"]
}

output "cognito_user_pool_id" {
  description = "Cognito user pool ID used for Runtime inbound JWT validation."
  value       = aws_cognito_user_pool.runtime.id
}

output "cognito_user_pool_domain" {
  description = "Cognito hosted UI domain prefix."
  value       = aws_cognito_user_pool_domain.runtime.domain
}

output "cognito_gateway_client_id" {
  description = "Cognito app client ID used by AgentCore Gateway for client credentials."
  value       = aws_cognito_user_pool_client.gateway_runtime.id
}

output "cognito_runtime_scope" {
  description = "OAuth scope used by AgentCore Gateway to invoke the Runtime."
  value       = local.cognito_scope_value
}

# ------------------------------------------------------------------
# RDS MCP Server outputs
# ------------------------------------------------------------------

output "rds_ecr_repository_url" {
  description = "ECR repository URL for the RDS MCP runtime image."
  value       = aws_ecr_repository.rds_runtime.repository_url
}

output "rds_runtime_arn" {
  description = "AgentCore Runtime ARN for the RDS MCP server."
  value       = aws_cloudformation_stack.rds_runtime.outputs["RuntimeArn"]
}

output "rds_runtime_status" {
  description = "Current AgentCore Runtime status for the RDS MCP server."
  value       = aws_cloudformation_stack.rds_runtime.outputs["RuntimeStatus"]
}

output "rds_gateway_target_id" {
  description = "Gateway target ID for the RDS MCP server."
  value       = aws_cloudformation_stack.rds_gateway_target.outputs["GatewayTargetId"]
}

output "rds_gateway_target_status" {
  description = "Gateway target status for the RDS MCP server."
  value       = aws_cloudformation_stack.rds_gateway_target.outputs["GatewayTargetStatus"]
}

output "rds_lambda_function_name" {
  description = "Lambda function name for the RDS query proxy."
  value       = aws_lambda_function.rds_query_proxy.function_name
}

output "rds_lambda_security_group_id" {
  description = "Security group ID of the RDS Lambda proxy (add ingress to RDS SG)."
  value       = aws_security_group.rds_lambda.id
}

# ------------------------------------------------------------------
# GitHub Actions MCP Server outputs
# ------------------------------------------------------------------

output "gha_ecr_repository_url" {
  description = "ECR repository URL for the GitHub Actions MCP runtime image."
  value       = aws_ecr_repository.gha_runtime.repository_url
}

output "gha_runtime_arn" {
  description = "AgentCore Runtime ARN for the GitHub Actions MCP server."
  value       = aws_cloudformation_stack.gha_runtime.outputs["RuntimeArn"]
}

output "gha_runtime_status" {
  description = "Current AgentCore Runtime status for the GitHub Actions MCP server."
  value       = aws_cloudformation_stack.gha_runtime.outputs["RuntimeStatus"]
}

output "gha_gateway_target_id" {
  description = "Gateway target ID for the GitHub Actions MCP server."
  value       = aws_cloudformation_stack.gha_gateway_target.outputs["GatewayTargetId"]
}

output "gha_gateway_target_status" {
  description = "Gateway target status for the GitHub Actions MCP server."
  value       = aws_cloudformation_stack.gha_gateway_target.outputs["GatewayTargetStatus"]
}

# ------------------------------------------------------------------
# Orchestrator MCP Server outputs
# ------------------------------------------------------------------

output "orchestrator_ecr_repository_url" {
  description = "ECR repository URL for the Orchestrator MCP runtime image."
  value       = aws_ecr_repository.orchestrator_runtime.repository_url
}

output "orchestrator_runtime_arn" {
  description = "AgentCore Runtime ARN for the Orchestrator MCP server."
  value       = aws_cloudformation_stack.orchestrator_runtime.outputs["RuntimeArn"]
}

output "orchestrator_runtime_status" {
  description = "Current AgentCore Runtime status for the Orchestrator MCP server."
  value       = aws_cloudformation_stack.orchestrator_runtime.outputs["RuntimeStatus"]
}

output "orchestrator_gateway_target_id" {
  description = "Gateway target ID for the Orchestrator MCP server."
  value       = aws_cloudformation_stack.orchestrator_gateway_target.outputs["GatewayTargetId"]
}

output "orchestrator_gateway_target_status" {
  description = "Gateway target status for the Orchestrator MCP server."
  value       = aws_cloudformation_stack.orchestrator_gateway_target.outputs["GatewayTargetStatus"]
}
