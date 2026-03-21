data "aws_iam_policy_document" "agentcore_assume_role" {
  statement {
    sid     = "AllowBedrockAgentCoreAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:*"]
    }
  }
}

resource "aws_iam_role" "runtime" {
  name               = trimsuffix(substr("${local.base_name}-agentcore-runtime", 0, 64), "-")
  assume_role_policy = data.aws_iam_policy_document.agentcore_assume_role.json
}

data "aws_iam_policy_document" "runtime" {
  statement {
    sid    = "EcrPull"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.runtime.arn]
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

  statement {
    sid    = "QueryApprovedLogGroups"
    effect = "Allow"
    actions = [
      "logs:GetQueryResults",
      "logs:StartQuery",
    ]
    resources = local.allowed_log_group_arns
  }

  statement {
    sid    = "ReadRuntimeConfigSecret"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [aws_secretsmanager_secret.runtime_config.arn]
  }
}

resource "terraform_data" "validate_target_app" {
  count = var.validate_target_app_configuration ? 1 : 0

  lifecycle {
    precondition {
      condition     = length(local.effective_allowed_log_group_names) > 0
      error_message = "Configure at least one target app log group. Set TF_VAR_default_log_group_name, or set TF_VAR_target_app_name with TF_VAR_target_app_environment, or set TF_VAR_allowed_log_group_names."
    }
  }
}

resource "aws_iam_role_policy" "runtime" {
  name   = trimsuffix(substr("${local.base_name}-agentcore-runtime", 0, 128), "-")
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime.json

  depends_on = [
    terraform_data.validate_target_app,
  ]
}

resource "aws_iam_role" "gateway" {
  name               = trimsuffix(substr("${local.base_name}-agentcore-gateway", 0, 64), "-")
  assume_role_policy = data.aws_iam_policy_document.agentcore_assume_role.json
}

data "aws_iam_policy_document" "gateway" {
  statement {
    sid    = "GetWorkloadAccessToken"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:GetWorkloadAccessToken",
    ]
    resources = [
      "arn:${local.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:workload-identity-directory/default",
      "arn:${local.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:workload-identity-directory/default/workload-identity/${local.gateway_name}-*",
    ]
  }

  statement {
    sid    = "GetResourceOauth2Token"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:GetResourceOauth2Token",
    ]
    resources = [
      "arn:${local.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:workload-identity-directory/default",
      "arn:${local.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:workload-identity-directory/default/workload-identity/${local.gateway_name}-*",
      "arn:${local.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:token-vault/default",
      data.external.agentcore_oauth_provider.result["credential_provider_arn"],
    ]
  }

  statement {
    sid    = "GetOauthClientSecret"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      data.external.agentcore_oauth_provider.result["secret_arn"],
    ]
  }
}

resource "aws_iam_role_policy" "gateway" {
  name   = trimsuffix(substr("${local.base_name}-agentcore-gateway", 0, 128), "-")
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.gateway.json
}
