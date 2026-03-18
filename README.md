# CloudWatch Logs Insights DevOps Agent on AgentCore

AWS Bedrock AgentCore Runtime 上で Python 製 MCP サーバーを動かし、AgentCore Gateway 経由で `query_cloudwatch_insights` を公開する構成です。

この版では Gateway から Runtime への接続に Cognito を使います。

関連ドキュメント:

- [プロジェクト構成ドキュメント](docs/project-structure.md)
- Runtime inbound auth: Cognito JWT (`CustomJWTAuthorizer`)
- Gateway target outbound auth: AgentCore Identity OAuth provider + Cognito client credentials
- クライアント接続先: AgentCore Gateway のみ
- CloudWatch への権限: Runtime IAM Role のみ
- CloudWatch 参照先: 単一 log group を基本、必要なら allow list 拡張

## ディレクトリ構成

```text
.
├── .env.example
├── Dockerfile
├── README.md
├── docs
│   └── project-structure.md
├── mcp.json
├── mcp_server.py
├── requirements.txt
└── terraform
    ├── agentcore_identity.tf
    ├── agentcore_runtime.tf
    ├── cognito.tf
    ├── ecr.tf
    ├── gateway.tf
    ├── iam.tf
    ├── locals.tf
    ├── outputs.tf
    ├── providers.tf
    ├── scripts
    │   └── manage_oauth_provider.py
    ├── templates
    │   ├── gateway.yaml.tftpl
    │   └── runtime.yaml.tftpl
    ├── terraform.tfvars.example
    ├── variables.tf
    └── versions.tf
```

## 構成図

```text
Codex / Copilot / VS Code
        |
        | MCP over Streamable HTTP
        v
AgentCore Gateway
        |
        | MCP target + OAuth(client_credentials)
        v
AgentCore Runtime
  - CustomJWTAuthorizer(Cognito)
  - mcp_server.py
        |
        | boto3
        v
CloudWatch Logs Insights
  - StartQuery
  - GetQueryResults
```

## `todo_sample` での対象 log group

`../todo_sample/infra/terraform/backend.tf` の定義から、backend の log group は次です。

```text
/ecs/todo-sample-prod-backend
```

## `.env` で対象アプリを切り替える

```bash
cp .env.example .env
```

`todo_sample` を見る例:

```dotenv
AWS_PROFILE=your-aws-profile
AWS_REGION=ap-northeast-1

TARGET_APP_NAME=todo-sample
TARGET_APP_ENV=prod
TARGET_APP_COMPONENT=backend
DEFAULT_LOG_GROUP_NAME=/ecs/todo-sample-prod-backend
ALLOWED_LOG_GROUP_NAMES=/ecs/todo-sample-prod-backend

TF_VAR_aws_region=ap-northeast-1
TF_VAR_project_name=devops-agent
TF_VAR_environment=prod
TF_VAR_python_executable=../.venv/bin/python
TF_VAR_target_app_name=todo-sample
TF_VAR_target_app_environment=prod
TF_VAR_target_app_component=backend
TF_VAR_default_log_group_name=/ecs/todo-sample-prod-backend
TF_VAR_runtime_image_tag=latest
```

ローカル MCP サーバーは `.env` を自動で読み込みます。Terraform は自動読込しないので、apply 前に export してください。

```bash
set -a
source .env
set +a
```

## ローカルで試す

### 1. Python 環境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 起動

```bash
set -a
source .env
set +a
python mcp_server.py
```

起動後:

- MCP endpoint: `http://localhost:8000/mcp`

`log_group_name` に `default` または `@default` を渡すと `.env` の既定 log group を使います。

## AWS へデプロイする手順

### 1. 事前準備

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
set -a
source .env
set +a
```

`terraform/scripts/manage_oauth_provider.py` が `boto3` で AgentCore Identity OAuth provider を作るため、`.venv` は必須です。

### 2. ECR だけ先に作る

```bash
cd terraform
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform init
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform apply \
  -target=aws_ecr_repository.runtime \
  -target=aws_ecr_lifecycle_policy.runtime
```

### 3. Runtime image を push

```bash
AWS_ACCOUNT_ID=$(AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" aws sts get-caller-identity --query Account --output text)
ECR_REPOSITORY_URL=$(AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform output -raw ecr_repository_url)

AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" aws ecr get-login-password --region "${AWS_REGION:-ap-northeast-1}" \
  | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION:-ap-northeast-1}.amazonaws.com"

docker buildx build \
  --platform linux/arm64 \
  -t "${ECR_REPOSITORY_URL}:latest" \
  --push \
  ..
```

AgentCore Runtime は `arm64` image を要求します。

### 4. Cognito + Runtime + Gateway を apply

```bash
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform apply
```

この apply で次を作ります。

- ECR repository
- Runtime IAM Role
- Gateway IAM Role
- Cognito User Pool
- Cognito Resource Server
- Cognito App Client
- Cognito User Pool Domain
- AgentCore Identity OAuth credential provider
- AgentCore Runtime
- AgentCore Gateway
- AgentCore GatewayTarget

### 5. 出力確認

```bash
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform output -raw gateway_url
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform output gateway_target_status
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform output cognito_user_pool_id
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform output cognito_runtime_scope
```

`gateway_target_status` が `READY` なら Gateway から Runtime の `tools/list` 同期まで成功しています。

## ツール仕様

- tool 名: `query_cloudwatch_insights`
- 引数:
  - `log_group_name`
  - `minutes`
  - `query`
- 返却値: JSON

返却例:

```json
{
  "ok": true,
  "region": "ap-northeast-1",
  "log_group_name": "/ecs/todo-sample-prod-backend",
  "minutes": 30,
  "query": "fields @timestamp, @message | sort @timestamp desc | limit 20",
  "query_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "status": "Complete",
  "results": [
    {
      "@timestamp": "2026-03-18 05:30:00.000",
      "@message": "Started GET /api/tasks"
    }
  ],
  "statistics": {
    "recordsMatched": 1.0
  }
}
```

## `todo_sample` 向けサンプル query

直近 20 件:

```sql
fields @timestamp, @message, @logStream
| sort @timestamp desc
| limit 20
```

Rails の 5xx / ERROR:

```sql
fields @timestamp, @message
| filter @message like /ERROR|FATAL|Completed 5\d\d/
| sort @timestamp desc
| limit 50
```

tasks API:

```sql
fields @timestamp, @message
| filter @message like /tasks/
| sort @timestamp desc
| limit 30
```

## Codex / Copilot 接続

### Codex

```bash
codex mcp add agentcoreCloudWatch --url "$(terraform -chdir=./terraform output -raw gateway_url)"
```

### VS Code / Copilot

`mcp.json` の `url` を `gateway_url` に置き換えます。

Gateway 経由で公開される tool 名には target 名の prefix が付きます。

この構成では:

```text
cwlogs___query_cloudwatch_insights
```

## IAM / Auth の考え方

- Runtime IAM Role:
  - ECR pull
  - Runtime logging / metrics / tracing
  - 許可された log group に対する `logs:StartQuery` / `logs:GetQueryResults`
- Runtime inbound auth:
  - Cognito の JWT を `CustomJWTAuthorizer` で検証
- Gateway target outbound auth:
  - AgentCore Identity OAuth credential provider
  - Cognito client credentials grant
  - Gateway IAM Role に次が必要
    - `bedrock-agentcore:GetWorkloadAccessToken`
    - `bedrock-agentcore:GetResourceOauth2Token`
    - `secretsmanager:GetSecretValue`
- Gateway inbound auth:
  - このサンプルでは `NONE`

## 注意点

### 1. 初回 apply 順序

ECR に image がないと Runtime 作成に失敗します。最初は ECR 作成 -> image push -> full apply の順です。

### 2. `arm64` image が必須

`linux/amd64` を push すると `Architecture incompatible` で Runtime 作成に失敗します。

### 3. `.venv` が必要

Terraform 中に `terraform/scripts/manage_oauth_provider.py` を実行して AgentCore Identity OAuth provider を作るため、`.venv/bin/python` と `boto3` が必要です。

### 4. log group の allow list

`.env` の `ALLOWED_LOG_GROUP_NAMES` と Terraform の IAM Policy がずれていると `AccessDenied` になります。

### 5. Gateway target の同期失敗

GatewayTarget は作成時に `tools/list` を呼びます。Runtime image 未反映、OAuth provider 未作成、Cognito scope 不一致のいずれかで `FAILED` になります。

### 6. Gateway は無認証

このサンプルはクライアント接続を簡単にするため Gateway inbound auth を `NONE` にしています。本番では Gateway 自体にも Cognito などの inbound auth を付けることを推奨します。

### 7. `tools/list` は通るのに `call_tool` だけ失敗する

`An internal error occurred. Please retry later.` の場合は、Gateway IAM Role の outbound OAuth 権限不足を疑ってください。

この構成では Gateway role に少なくとも次が必要です。

- `bedrock-agentcore:GetWorkloadAccessToken`
- `bedrock-agentcore:GetResourceOauth2Token`
- `secretsmanager:GetSecretValue`

`GetResourceOauth2Token` は次の resource 群で評価されました。

- `arn:aws:bedrock-agentcore:${region}:${account_id}:workload-identity-directory/default`
- `arn:aws:bedrock-agentcore:${region}:${account_id}:workload-identity-directory/default/workload-identity/${gateway_name}-*`
- `arn:aws:bedrock-agentcore:${region}:${account_id}:token-vault/default`
- `oauth2credentialprovider` の provider ARN

## 参考コマンド

Gateway URL:

```bash
cd terraform
AWS_PROFILE=your-aws-profile terraform output -raw gateway_url
```

Runtime target URL:

```bash
cd terraform
AWS_PROFILE=your-aws-profile terraform output -raw runtime_mcp_invoke_url
```

許可 log group:

```bash
cd terraform
AWS_PROFILE=your-aws-profile terraform output allowed_log_group_names
```

Cognito scope:

```bash
cd terraform
AWS_PROFILE=your-aws-profile terraform output cognito_runtime_scope
```
