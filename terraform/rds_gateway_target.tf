# ------------------------------------------------------------------
# RDS MCP Server – Gateway Target (added to the existing Gateway)
# ------------------------------------------------------------------

resource "aws_cloudformation_stack" "rds_gateway_target" {
  name               = local.rds_gateway_target_stack_name
  timeout_in_minutes = 20

  template_body = templatefile("${path.module}/templates/rds_gateway_target.yaml.tftpl", {
    gateway_identifier = aws_cloudformation_stack.gateway.outputs["GatewayIdentifier"]
    target_name        = local.rds_target_name
    runtime_mcp_invoke_url = local.rds_runtime_mcp_invoke_url
    oauth_provider_arn     = data.external.agentcore_oauth_provider.result["credential_provider_arn"]
    oauth_scope            = local.cognito_scope_value
  })

  depends_on = [
    aws_cloudformation_stack.gateway,
    aws_cloudformation_stack.rds_runtime,
    terraform_data.agentcore_oauth_provider,
  ]
}
