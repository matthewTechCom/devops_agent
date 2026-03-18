resource "aws_cognito_user_pool" "runtime" {
  name = "${local.base_name}-runtime-pool"
}

resource "aws_cognito_resource_server" "runtime" {
  identifier   = "agentcore-runtime-${random_string.suffix.result}"
  name         = "${local.base_name}-runtime"
  user_pool_id = aws_cognito_user_pool.runtime.id

  scope {
    scope_name        = var.cognito_scope_name
    scope_description = "Invoke the AgentCore Runtime MCP endpoint."
  }
}

resource "aws_cognito_user_pool_client" "gateway_runtime" {
  name         = var.cognito_gateway_client_name
  user_pool_id = aws_cognito_user_pool.runtime.id

  generate_secret                      = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = [local.cognito_scope_value]
  supported_identity_providers         = ["COGNITO"]
  prevent_user_existence_errors        = "ENABLED"
}

resource "aws_cognito_user_pool_domain" "runtime" {
  domain       = local.cognito_domain_prefix
  user_pool_id = aws_cognito_user_pool.runtime.id
}
