# ------------------------------------------------------------------
# Orchestrator MCP Server – Gateway Target
# ------------------------------------------------------------------

resource "aws_cloudformation_stack" "orchestrator_gateway_target" {
  name               = local.orchestrator_gateway_target_stack_name
  timeout_in_minutes = 20

  template_body = templatefile("${path.module}/templates/orchestrator_gateway_target.yaml.tftpl", {
    gateway_identifier     = aws_cloudformation_stack.gateway.outputs["GatewayIdentifier"]
    target_name            = local.orchestrator_target_name
    runtime_mcp_invoke_url = local.orchestrator_runtime_mcp_invoke_url
    oauth_provider_arn     = data.external.agentcore_oauth_provider.result["credential_provider_arn"]
    oauth_scope            = local.cognito_scope_value
  })

  depends_on = [
    aws_cloudformation_stack.gateway,
    aws_cloudformation_stack.orchestrator_runtime,
    terraform_data.agentcore_oauth_provider,
  ]
}
