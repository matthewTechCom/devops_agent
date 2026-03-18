resource "aws_cloudformation_stack" "gateway" {
  name               = local.gateway_stack_name
  timeout_in_minutes = 20

  template_body = templatefile("${path.module}/templates/gateway.yaml.tftpl", {
    gateway_name           = local.gateway_name
    gateway_description    = local.gateway_stack_description
    gateway_role_arn       = aws_iam_role.gateway.arn
    gateway_instructions   = local.gateway_instructions
    target_name            = local.target_name
    runtime_mcp_invoke_url = local.runtime_mcp_invoke_url
    oauth_provider_arn     = data.external.agentcore_oauth_provider.result["credential_provider_arn"]
    oauth_scope            = local.cognito_scope_value
  })

  depends_on = [
    aws_cloudformation_stack.runtime,
    terraform_data.agentcore_oauth_provider,
  ]
}
