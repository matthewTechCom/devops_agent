# ------------------------------------------------------------------
# Lambda Proxy – runs inside the todo_sample VPC to query RDS
# ------------------------------------------------------------------

data "archive_file" "rds_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../rds_lambda"
  output_path = "${path.module}/.build/rds_lambda.zip"
}

# Lambda Layer for psycopg2-binary (built separately or use a public layer)
# For simplicity we bundle dependencies in the zip via a build step.
# See: rds_lambda_build null_resource below.

resource "aws_iam_role" "rds_lambda" {
  name = trimsuffix(substr("${local.base_name}-rds-lambda", 0, 64), "-")

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

data "aws_iam_policy_document" "rds_lambda" {
  # CloudWatch Logs for Lambda execution logs
  statement {
    sid    = "LambdaLogging"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda/*"]
  }

  # VPC networking (ENI management)
  statement {
    sid    = "VpcNetworking"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
    ]
    resources = ["*"]
  }

  # Read DATABASE_URL from Secrets Manager
  statement {
    sid    = "ReadDbSecret"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [var.rds_database_url_secret_arn]
  }
}

resource "aws_iam_role_policy" "rds_lambda" {
  name   = trimsuffix(substr("${local.base_name}-rds-lambda", 0, 128), "-")
  role   = aws_iam_role.rds_lambda.id
  policy = data.aws_iam_policy_document.rds_lambda.json
}

# Security group for the Lambda function (inside todo_sample VPC)
resource "aws_security_group" "rds_lambda" {
  name        = "${local.base_name}-rds-lambda-sg"
  description = "Security group for RDS query Lambda proxy"
  vpc_id      = var.rds_vpc_id

  egress {
    description = "All outbound (DB + Secrets Manager endpoint)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.tags
}

# Build Lambda package with dependencies
resource "terraform_data" "rds_lambda_build" {
  triggers_replace = {
    source_hash = data.archive_file.rds_lambda.output_base64sha256
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      BUILD_DIR="${path.module}/.build/rds_lambda_pkg"
      rm -rf "$BUILD_DIR"
      mkdir -p "$BUILD_DIR"

      "${var.python_executable}" -m pip install \
        --target "$BUILD_DIR" \
        --platform manylinux2014_aarch64 \
        --implementation cp \
        --python-version 3.12 \
        --only-binary=:all: \
        -r "${path.module}/../rds_lambda/requirements.txt" \
        --quiet

      cp "${path.module}/../rds_lambda/lambda_handler.py" "$BUILD_DIR/"

      OUTPUT_ZIP="$(cd "${path.module}/.build" && pwd)/rds_lambda_deploy.zip"
      cd "$BUILD_DIR"
      zip -r9 "$OUTPUT_ZIP" . -q
    EOT
  }
}

resource "aws_lambda_function" "rds_query_proxy" {
  function_name = "${local.base_name}-rds-query-proxy"
  description   = "Lambda proxy for read-only RDS PostgreSQL queries from DevOps Agent"
  role          = aws_iam_role.rds_lambda.arn
  handler       = "lambda_handler.lambda_handler"
  runtime       = "python3.12"
  architectures = ["arm64"]
  timeout       = 60
  memory_size   = 256
  filename      = "${path.module}/.build/rds_lambda_deploy.zip"

  source_code_hash = data.archive_file.rds_lambda.output_base64sha256

  vpc_config {
    subnet_ids         = var.rds_lambda_subnet_ids
    security_group_ids = [aws_security_group.rds_lambda.id]
  }

  environment {
    variables = {
      DB_SECRET_ARN        = var.rds_database_url_secret_arn
      STATEMENT_TIMEOUT_MS = tostring(var.rds_statement_timeout_ms)
      MAX_ROWS             = tostring(var.rds_max_rows)
    }
  }

  tags = local.tags

  depends_on = [
    aws_iam_role_policy.rds_lambda,
    terraform_data.rds_lambda_build,
  ]
}

resource "aws_cloudwatch_log_group" "rds_lambda" {
  name              = "/aws/lambda/${aws_lambda_function.rds_query_proxy.function_name}"
  retention_in_days = 14
  tags              = local.tags
}

# Allow Lambda SG → RDS SG on port 5432
resource "aws_security_group_rule" "rds_from_lambda" {
  count = var.rds_db_security_group_id != "" ? 1 : 0

  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = var.rds_db_security_group_id
  source_security_group_id = aws_security_group.rds_lambda.id
  description              = "PostgreSQL from RDS Lambda proxy"
}

# Allow Lambda SG → VPC Endpoints SG on port 443 (Secrets Manager)
resource "aws_security_group_rule" "vpce_from_lambda" {
  count = var.rds_vpce_security_group_id != "" ? 1 : 0

  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = var.rds_vpce_security_group_id
  source_security_group_id = aws_security_group.rds_lambda.id
  description              = "HTTPS from RDS Lambda proxy to VPC endpoints"
}
