resource "terraform_data" "agentcore_oauth_provider" {
  triggers_replace = {
    python_executable      = var.python_executable
    script_path            = "${path.module}/scripts/manage_oauth_provider.py"
    region                 = var.aws_region
    name                   = local.oauth_provider_name
    client_id              = aws_cognito_user_pool_client.gateway_runtime.id
    client_secret_sha256   = sha256(aws_cognito_user_pool_client.gateway_runtime.client_secret)
    authorization_endpoint = local.cognito_authorization_endpoint
    token_endpoint         = local.cognito_token_endpoint
    issuer                 = local.cognito_issuer
  }

  provisioner "local-exec" {
    command = <<-EOT
      "${var.python_executable}" "${path.module}/scripts/manage_oauth_provider.py" upsert \
        --region "${var.aws_region}" \
        --name "${local.oauth_provider_name}" \
        --vendor "CognitoOauth2" \
        --client-id "${aws_cognito_user_pool_client.gateway_runtime.id}" \
        --authorization-endpoint "${local.cognito_authorization_endpoint}" \
        --token-endpoint "${local.cognito_token_endpoint}" \
        --issuer "${local.cognito_issuer}"
    EOT

    environment = {
      AGENTCORE_OAUTH_CLIENT_SECRET = aws_cognito_user_pool_client.gateway_runtime.client_secret
    }
  }

  provisioner "local-exec" {
    when    = destroy
    command = "\"${self.triggers_replace.python_executable}\" \"${self.triggers_replace.script_path}\" delete --region \"${self.triggers_replace.region}\" --name \"${self.triggers_replace.name}\""
  }
}

data "external" "agentcore_oauth_provider" {
  depends_on = [terraform_data.agentcore_oauth_provider]

  program = [
    var.python_executable,
    "${path.module}/scripts/manage_oauth_provider.py",
    "get",
    "--region",
    var.aws_region,
    "--name",
    local.oauth_provider_name,
  ]
}
