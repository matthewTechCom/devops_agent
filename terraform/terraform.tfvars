aws_region   = "us-east-1"
project_name = "devops-agent"
environment  = "prod"

python_executable = "../.venv/bin/python"

runtime_image_tag = "latest"

target_app_name        = "todo-sample"
target_app_environment = "prod"
target_app_component   = "backend"

common_tags = {
  Owner = "platform-team"
}

# ------------------------------------------------------------------
# RDS MCP Server – todo_sample infrastructure references
# ------------------------------------------------------------------

rds_runtime_image_tag = "latest"

# todo_sample VPC
rds_vpc_id = "vpc-0a0bda9be070a2cba"

# todo_sample private app subnets (Lambda runs here to reach RDS)
rds_lambda_subnet_ids = [
  "subnet-09ddff59f81673277",
  "subnet-0b6e8de615accbfd8",
]

# Secrets Manager ARN for DATABASE_URL (postgresql://...)
rds_database_url_secret_arn = "arn:aws:secretsmanager:us-east-1:692185846024:secret:todo-sample-prod/backend/database_url-cxRxNj"

# RDS security group (Lambda needs ingress on port 5432)
rds_db_security_group_id = "sg-0d4248461176c6dda"

# VPC endpoints security group (Lambda needs ingress on port 443 for Secrets Manager)
rds_vpce_security_group_id = "sg-00f4fbda6538638a7"

# ------------------------------------------------------------------
# GitHub Actions MCP Server
# ------------------------------------------------------------------

gha_runtime_image_tag = "latest"
github_pat_secret_arn = "arn:aws:secretsmanager:us-east-1:692185846024:secret:devops-agent-github-pat-r8cWBS"
github_repository     = "matthewTechCom/todo_sample"

# ------------------------------------------------------------------
# Orchestrator MCP Server
# ------------------------------------------------------------------

orchestrator_runtime_image_tag  = "latest"
orchestrator_bedrock_model_id   = "us.anthropic.claude-sonnet-4-20250514-v1:0"
orchestrator_max_react_steps    = 20
orchestrator_bedrock_max_tokens = 4096
