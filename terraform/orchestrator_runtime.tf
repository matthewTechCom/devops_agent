# ------------------------------------------------------------------
# Orchestrator MCP Server – AgentCore Runtime
# ------------------------------------------------------------------

# Runtime config secret
resource "aws_secretsmanager_secret" "orchestrator_runtime_config" {
  name        = local.orchestrator_runtime_config_secret_name
  description = "Runtime configuration for ${local.orchestrator_runtime_name}."
}

resource "terraform_data" "orchestrator_runtime_config_secret_value" {
  triggers_replace = {
    python_executable = var.python_executable
    script_path       = "${path.module}/scripts/manage_runtime_config_secret.py"
    region            = var.aws_region
    secret_id         = aws_secretsmanager_secret.orchestrator_runtime_config.id
    payload_sha256    = sha256(jsonencode(local.orchestrator_runtime_config_secret_payload))
  }

  provisioner "local-exec" {
    command = <<-EOT
      "${var.python_executable}" "${path.module}/scripts/manage_runtime_config_secret.py" upsert \
        --region "${var.aws_region}" \
        --secret-id "${aws_secretsmanager_secret.orchestrator_runtime_config.id}"
    EOT

    environment = {
      RUNTIME_CONFIG_SECRET_STRING = jsonencode(local.orchestrator_runtime_config_secret_payload)
    }
  }
}

# IAM role for the Orchestrator Runtime
resource "aws_iam_role" "orchestrator_runtime" {
  name               = trimsuffix(substr("${local.base_name}-agentcore-orch-rt", 0, 64), "-")
  assume_role_policy = data.aws_iam_policy_document.agentcore_assume_role.json
}

data "aws_iam_policy_document" "orchestrator_runtime" {
  statement {
    sid    = "EcrPull"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.orchestrator_runtime.arn]
  }

  statement {
    sid       = "EcrAuthorizationToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "RuntimeLoggingWrite"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
      "logs:PutLogEvents",
    ]
    resources = [
      "*",
      local.runtime_log_group_arn,
      local.runtime_log_stream_arn,
    ]
  }

  statement {
    sid    = "RuntimeMetricsWrite"
    effect = "Allow"
    actions = [
      "cloudwatch:PutMetricData",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }

  statement {
    sid    = "RuntimeTracingWrite"
    effect = "Allow"
    actions = [
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
      "xray:PutTelemetryRecords",
      "xray:PutTraceSegments",
    ]
    resources = ["*"]
  }

  # Bedrock InvokeModel for Claude
  statement {
    sid    = "BedrockInvokeModel"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      "arn:${local.partition}:bedrock:${var.aws_region}::foundation-model/${var.orchestrator_bedrock_model_id}",
      "arn:${local.partition}:bedrock:${var.aws_region}::foundation-model/anthropic.*",
    ]
  }

  # Read runtime config from Secrets Manager
  statement {
    sid    = "ReadOrchestratorRuntimeConfigSecret"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [aws_secretsmanager_secret.orchestrator_runtime_config.arn]
  }
}

resource "aws_iam_role_policy" "orchestrator_runtime" {
  name   = trimsuffix(substr("${local.base_name}-agentcore-orch-rt", 0, 128), "-")
  role   = aws_iam_role.orchestrator_runtime.id
  policy = data.aws_iam_policy_document.orchestrator_runtime.json
}

# ECR repository for the Orchestrator MCP Server container
resource "aws_ecr_repository" "orchestrator_runtime" {
  name                 = local.orchestrator_runtime_repository_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "orchestrator_runtime" {
  repository = aws_ecr_repository.orchestrator_runtime.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the most recent 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# CloudFormation stack for the Orchestrator Runtime
resource "aws_cloudformation_stack" "orchestrator_runtime" {
  name               = local.orchestrator_runtime_stack_name
  timeout_in_minutes = 30

  template_body = templatefile("${path.module}/templates/orchestrator_runtime.yaml.tftpl", {
    runtime_name                 = local.orchestrator_runtime_name
    runtime_description          = local.orchestrator_runtime_stack_description
    runtime_role_arn             = aws_iam_role.orchestrator_runtime.arn
    runtime_image_uri            = local.orchestrator_runtime_image_uri
    aws_region                   = var.aws_region
    bedrock_model_id             = var.orchestrator_bedrock_model_id
    gateway_mcp_url              = "${aws_cloudformation_stack.gateway.outputs["GatewayUrl"]}/mcp"
    max_react_steps              = tostring(var.orchestrator_max_react_steps)
    bedrock_max_tokens           = tostring(var.orchestrator_bedrock_max_tokens)
    runtime_idle_timeout_seconds = tostring(var.runtime_idle_timeout_seconds)
    runtime_max_lifetime_seconds = tostring(var.runtime_max_lifetime_seconds)
    runtime_config_secret_id     = aws_secretsmanager_secret.orchestrator_runtime_config.arn
    runtime_discovery_url        = local.cognito_discovery_url
    runtime_allowed_client       = aws_cognito_user_pool_client.gateway_runtime.id
    runtime_allowed_scope        = local.cognito_scope_value
  })

  depends_on = [
    aws_iam_role_policy.orchestrator_runtime,
    aws_cognito_user_pool_domain.runtime,
    terraform_data.orchestrator_runtime_config_secret_value,
  ]
}
