# CloudWatch Logs Insights DevOps Agent on AgentCore

AWS Bedrock AgentCore Runtime 上で Python 製 MCP サーバーを動かし、AgentCore Gateway 経由で `query_cloudwatch_insights` を公開する構成です。Gateway のクライアント向け認証は `AWS_IAM` を使い、MCP Proxy for AWS から SigV4 で接続する前提です。

関連ドキュメント:

- [プロジェクト構成ドキュメント](docs/project-structure.md)

## ディレクトリ構成

```text
.
├── .env.example
├── .dockerignore
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
    ├── runtime_config.tf
    ├── scripts
    │   ├── manage_oauth_provider.py
    │   └── manage_runtime_config_secret.py
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
        | stdio
        v
MCP Proxy for AWS
        |
        | MCP over Streamable HTTP + SigV4
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

## 環境変数設定


### `.env` に設定する環境変数

- `AWS_PROFILE`
  - AWS CLI / Terraform / 補助スクリプトが使うローカルの認証プロファイル名です。
  - 例: `your-aws-profile`
- `AWS_REGION`
  - AWS のデプロイ先リージョンです。
  - ローカル MCP サーバーの `boto3` でも使います。
  - 例: `us-east-1`
- `TARGET_APP_NAME`
  - 監視対象アプリの名前です。
  - `DEFAULT_LOG_GROUP_NAME` を省略したときの log group 名導出に使います。
  - 例: `todo-sample`
- `TARGET_APP_ENV`
  - 監視対象アプリの環境名です。
  - 例: `prod`
- `TARGET_APP_COMPONENT`
  - 監視対象コンポーネント名です。
  - 例: `backend`
- `DEFAULT_LOG_GROUP_NAME`
  - 既定の CloudWatch Logs log group 名です。
  - `query_cloudwatch_insights` に `default` または `@default` を渡したときに使われます。
  - 省略時は `TARGET_APP_*` から `/ecs/{app}-{env}-{component}` を導出します。
- `ALLOWED_LOG_GROUP_NAMES`
  - クエリ実行を許可する log group のカンマ区切り一覧です。
  - アプリ側の入力制御と IAM Policy の両方で使います。
  - 例: `/ecs/todo-sample-prod-backend,/ecs/another-app-prod-backend`
- `TF_VAR_aws_region`
  - Terraform の `aws_region` 変数に渡す値です。
- `TF_VAR_project_name`
  - Terraform の `project_name` 変数です。
  - 作成される ECR / IAM / Runtime / Gateway の命名に使います。
- `TF_VAR_environment`
  - Terraform の `environment` 変数です。
  - 例: `prod`
- `TF_VAR_python_executable`
  - Terraform の `local-exec` / `external` が使う Python 実行ファイルです。
  - 通常は `../.venv/bin/python` を使います。
- `TF_VAR_target_app_name`
  - Terraform 側で使う監視対象アプリ名です。
  - ふつうは `TARGET_APP_NAME` と同じ値にします。
- `TF_VAR_target_app_environment`
  - Terraform 側で使う監視対象環境名です。
- `TF_VAR_target_app_component`
  - Terraform 側で使う監視対象コンポーネント名です。
- `TF_VAR_default_log_group_name`
  - Terraform 側で使う既定 log group 名です。
  - IAM Policy と runtime config に反映されます。
- `TF_VAR_gateway_authorizer_type`
  - AgentCore Gateway の inbound auth 種別です。
  - MCP Proxy for AWS を使う場合は `AWS_IAM` を指定します。
- `TF_VAR_gateway_invoke_role_names`
  - Terraform が `bedrock-agentcore:InvokeGateway` ポリシーを自動アタッチする既存 IAM Role 名の一覧です。
  - 例: `["my-devbox-role"]`
- `TF_VAR_runtime_image_tag`
  - AgentCore Runtime にデプロイするコンテナ image tag です。
  - `docker buildx build` の tag と一致させる必要があります。
  - `TF_VAR_runtime_image_tag` を export している場合は、Terraform の default よりそちらが優先されます。
  - Terraform はこの tag から ECR の digest を解決し、Runtime には `repo@sha256:...` を渡します。

```bash
cp .env.example .env
```

ローカル MCP サーバーは `.env` を自動で読み込みます。Terraform は自動読込しないので、apply 前に export してください。

AWS 上の AgentCore Runtime では、対象アプリ設定を Runtime 環境変数へ直接埋め込まず、Terraform が作成する Secrets Manager の runtime config を起動時に読み込みます。

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

`terraform/scripts/manage_oauth_provider.py` と `terraform/scripts/manage_runtime_config_secret.py` が `boto3` で AWS API を呼ぶため、`.venv` は必須です。

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
IMAGE_TAG="${TF_VAR_runtime_image_tag:-latest}"

AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" aws ecr get-login-password --region "${AWS_REGION:-us-east-1}" \
  | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION:-us-east-1}.amazonaws.com"

docker buildx build \
  --platform linux/arm64 \
  --provenance=false \
  --sbom=false \
  -t "${ECR_REPOSITORY_URL}:${IMAGE_TAG}" \
  --push \
  ..
```

AgentCore Runtime は `arm64` image を要求します。
Terraform apply 時には、push 済み tag の digest を ECR から引いて Runtime に渡します。
`TF_VAR_runtime_image_tag` を export している場合は、ここで使う `IMAGE_TAG` も同じ値になります。

### 4. Cognito + Runtime + Gateway を apply

```bash
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform apply
```

この apply で次を作ります。

- ECR repository
- Runtime config secret (Secrets Manager)
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
- Gateway invoke 用 managed IAM policy

### 5. 出力確認

```bash
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform output -raw gateway_url
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform output -raw gateway_authorizer_type
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform output -raw gateway_invoke_policy_arn
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform output gateway_target_status
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform output runtime_config_secret_arn
```

`gateway_target_status` が `READY` なら Gateway から Runtime の `tools/list` 同期まで成功しています。

`gateway_authorizer_type` が `AWS_IAM` なら、クライアントは SigV4 署名付きで Gateway を呼ぶ必要があります。`gateway_invoke_policy_arn` はその caller role に付与するための managed policy です。

## MCP Proxy for AWS から接続する

`mcp.json` のサンプルは `uvx mcp-proxy-for-aws@latest` を使う前提に更新しています。必要な AWS 権限は `bedrock-agentcore:InvokeGateway` です。

Terraform で caller role へ自動付与しない場合は、次で policy JSON を取得できます。

```bash
AWS_PROFILE="${AWS_PROFILE:-your-aws-profile}" terraform output -raw gateway_invoke_policy_document
```

接続元の AWS 認証情報は、次のいずれかを使います。

- `AWS_PROFILE`
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`
- EC2 / ECS / Lambda などの IAM role

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
  "region": "us-east-1",
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

Terraform 中に `terraform/scripts/manage_oauth_provider.py` と `terraform/scripts/manage_runtime_config_secret.py` を実行するため、`.venv/bin/python` と `boto3` が必要です。

### 4. Runtime config の読み込み元

AgentCore Runtime 上では `TARGET_APP_*` や `DEFAULT_LOG_GROUP_NAME` を直接環境変数へ入れず、Secrets Manager の JSON を `RUNTIME_CONFIG_SECRET_ID` 経由で読み込みます。ローカル実行では従来どおり `.env` を使います。

### 5. log group の allow list

`.env` の `ALLOWED_LOG_GROUP_NAMES` と Terraform の IAM Policy がずれていると `AccessDenied` になります。

### 6. Gateway target の同期失敗

GatewayTarget は作成時に `tools/list` を呼びます。Runtime image 未反映、OAuth provider 未作成、Cognito scope 不一致のいずれかで `FAILED` になります。

### 7. Gateway は無認証

このサンプルはクライアント接続を簡単にするため Gateway inbound auth を `NONE` にしています。本番では Gateway 自体にも Cognito などの inbound auth を付けることを推奨します。

### 8. `tools/list` は通るのに `call_tool` だけ失敗する

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

Gateway invoke policy:

```bash
cd terraform
AWS_PROFILE=your-aws-profile terraform output -raw gateway_invoke_policy_arn
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
