resource "aws_cloudformation_stack" "runtime" {
  name               = local.runtime_stack_name
  timeout_in_minutes = 30

  template_body = templatefile("${path.module}/templates/runtime.yaml.tftpl", {
    runtime_name                 = local.runtime_name
    runtime_description          = local.runtime_stack_description
    runtime_role_arn             = aws_iam_role.runtime.arn
    runtime_image_uri            = local.runtime_image_uri
    aws_region                   = var.aws_region
    query_timeout_seconds        = tostring(var.query_timeout_seconds)
    query_poll_seconds           = tostring(var.query_poll_seconds)
    runtime_idle_timeout_seconds = tostring(var.runtime_idle_timeout_seconds)
    runtime_max_lifetime_seconds = tostring(var.runtime_max_lifetime_seconds)
    target_app_name              = coalesce(var.target_app_name, "")
    target_app_environment       = coalesce(var.target_app_environment, "")
    target_app_component         = var.target_app_component
    default_log_group_name       = coalesce(local.effective_default_log_group_name, "")
    allowed_log_group_names      = local.effective_allowed_log_group_names_csv
    runtime_discovery_url        = local.cognito_discovery_url
    runtime_allowed_client       = aws_cognito_user_pool_client.gateway_runtime.id
    runtime_allowed_scope        = local.cognito_scope_value
  })

  depends_on = [
    aws_iam_role_policy.runtime,
    aws_cognito_user_pool_domain.runtime,
  ]
}
