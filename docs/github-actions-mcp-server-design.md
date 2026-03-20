# GitHub Actions MCP Server 設計書

## 概要

GitHub Actions のワークフロー実行履歴・ログをAgent Coreから調査できるMCPサーバーを新規作成する。デプロイ失敗時の原因調査や、CI/CDパイプラインの状態監視をAIエージェント経由で行えるようにする。

---

## 現状構成

```mermaid
graph LR
    Client["MCP Client<br/>(Codex / VS Code / Copilot)"]
    GW["AgentCore Gateway"]
    RT_CW["Target: cwlogs<br/>CloudWatch MCP"]
    RT_RDS["Target: rdsquery<br/>RDS Query MCP"]
    CW["CloudWatch Logs"]
    RDS["RDS PostgreSQL"]

    Client -->|SigV4 via MCP Proxy for AWS| GW
    GW --> RT_CW
    GW --> RT_RDS
    RT_CW --> CW
    RT_RDS --> RDS
```

## 目標構成

```mermaid
graph LR
    Client["MCP Client<br/>(Codex / VS Code / Copilot)"]
    GW["AgentCore Gateway"]
    RT_CW["Target: cwlogs<br/>CloudWatch MCP"]
    RT_RDS["Target: rdsquery<br/>RDS Query MCP"]
    RT_GHA["Target: ghactions<br/>GitHub Actions MCP"]
    CW["CloudWatch Logs"]
    RDS["RDS PostgreSQL"]
    GH["GitHub API"]

    Client -->|SigV4 via MCP Proxy for AWS| GW
    GW --> RT_CW
    GW --> RT_RDS
    GW --> RT_GHA
    RT_CW --> CW
    RT_RDS --> RDS
    RT_GHA -->|REST API| GH

    style RT_GHA fill:#f9e79f,stroke:#f39c12,stroke-width:2px
    style GH fill:#d5f5e3,stroke:#27ae60,stroke-width:2px
```

---

## アーキテクチャ詳細

### RDS MCP との構成比較

```mermaid
graph TB
    subgraph "RDS MCP（Lambda Proxy パターン）"
        RT_RDS["AgentCore Runtime<br/>(Public Network)"]
        Lambda["Lambda<br/>(VPC内)"]
        RDS2["RDS<br/>(Private Subnet)"]
        RT_RDS -->|lambda.invoke| Lambda
        Lambda -->|psycopg2| RDS2
    end

    subgraph "GitHub Actions MCP（直接API パターン）"
        RT_GHA["AgentCore Runtime<br/>(Public Network)"]
        GHAPI["GitHub REST API<br/>(api.github.com)"]
        RT_GHA -->|HTTPS + PAT| GHAPI
    end

    style RT_GHA fill:#f9e79f,stroke:#f39c12,stroke-width:2px
```

**GitHub APIはパブリックエンドポイント**のため、RDSのようなLambda Proxyは不要。AgentCore Runtimeから直接HTTPSで接続できる。

### コンポーネント図

```mermaid
graph TB
    subgraph "MCP Client"
        IDE["VS Code / Copilot / Codex"]
    end

    subgraph "AWS - AgentCore"
        GW["AgentCore Gateway<br/>Protocol: MCP"]

        subgraph "Target: cwlogs"
            RT1["Runtime<br/>mcp_server.py"]
        end

        subgraph "Target: rdsquery"
            RT2["Runtime<br/>rds_mcp_server.py"]
        end

        subgraph "Target: ghactions（新規）"
            RT3["Runtime<br/>gha_mcp_server.py"]
        end
    end

    subgraph "AWS - Secrets"
        SM_GH["Secrets Manager<br/>GitHub PAT"]
    end

    subgraph "External"
        GHAPI["GitHub REST API<br/>api.github.com"]
    end

    IDE -->|Streamable HTTP + SigV4| GW
    GW -->|cwlogs___*| RT1
    GW -->|rdsquery___*| RT2
    GW -->|ghactions___*| RT3

    RT3 -->|GetSecretValue| SM_GH
    RT3 -->|HTTPS + Bearer| GHAPI

    style RT3 fill:#f9e79f,stroke:#f39c12,stroke-width:2px
```

---

## 認証フロー

```mermaid
sequenceDiagram
    participant RT as GHA MCP Runtime
    participant SM as Secrets Manager
    participant GH as GitHub API

    Note over RT: コンテナ起動時
    RT->>SM: GetSecretValue(github-pat-secret)
    SM-->>RT: GitHub Personal Access Token

    Note over RT: ツール呼び出し時
    RT->>GH: GET /repos/{owner}/{repo}/actions/runs<br/>Authorization: Bearer {PAT}
    GH-->>RT: Workflow Runs JSON
```

### GitHub PAT に必要なスコープ

| スコープ | 理由 |
|---|---|
| `actions:read` | ワークフロー実行履歴・ログの取得 |
| `contents:read` | ワークフロー定義ファイルの参照（任意） |

**Fine-grained PAT** を推奨（対象リポジトリを限定可能）。

---

## MCP Server 設計

### 提供ツール一覧

```mermaid
graph LR
    subgraph "ghactions ツール"
        T1["list_workflow_runs<br/>実行履歴一覧"]
        T2["get_workflow_run<br/>実行詳細"]
        T3["get_job_logs<br/>ジョブログ取得"]
        T4["list_workflows<br/>ワークフロー一覧"]
        T5["get_workflow_run_jobs<br/>ジョブ一覧"]
    end
```

| ツール名 | 説明 | パラメータ |
|---|---|---|
| `list_workflow_runs` | ワークフロー実行履歴を取得 | `status` (optional), `branch` (optional), `limit` (default: 10) |
| `get_workflow_run` | 特定の実行の詳細を取得 | `run_id: int` |
| `get_workflow_run_jobs` | 実行内のジョブ一覧とステータス | `run_id: int` |
| `get_job_logs` | 特定ジョブのログを取得 | `job_id: int`, `tail_lines` (default: 100) |
| `list_workflows` | リポジトリのワークフロー定義一覧 | なし |

### ツール詳細

#### `list_workflow_runs`

```python
@mcp.tool()
def list_workflow_runs(
    status: str = "",
    branch: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """
    GitHub Actionsのワークフロー実行履歴を取得する。
    status: "completed", "in_progress", "queued", "failure", "success" など
    branch: フィルタするブランチ名
    """
```

**レスポンス例:**
```json
{
  "ok": true,
  "repository": "matthewTechCom/todo_sample",
  "total_count": 25,
  "runs": [
    {
      "id": 12345678,
      "name": "Deploy Backend",
      "status": "completed",
      "conclusion": "failure",
      "branch": "main",
      "commit_sha": "abc1234",
      "commit_message": "Fix API endpoint",
      "actor": "shimizuyuhri",
      "created_at": "2026-03-20T10:00:00Z",
      "updated_at": "2026-03-20T10:05:30Z",
      "run_duration_seconds": 330,
      "url": "https://github.com/matthewTechCom/todo_sample/actions/runs/12345678"
    }
  ]
}
```

#### `get_workflow_run`

```python
@mcp.tool()
def get_workflow_run(run_id: int) -> dict[str, Any]:
    """特定のワークフロー実行の詳細情報を取得する。"""
```

**レスポンス例:**
```json
{
  "ok": true,
  "run": {
    "id": 12345678,
    "name": "Deploy Backend",
    "status": "completed",
    "conclusion": "failure",
    "branch": "main",
    "event": "push",
    "commit_sha": "abc1234",
    "commit_message": "Fix API endpoint",
    "actor": "shimizuyuhri",
    "triggering_actor": "shimizuyuhri",
    "created_at": "2026-03-20T10:00:00Z",
    "run_attempt": 1,
    "jobs_url": "https://api.github.com/repos/.../actions/runs/12345678/jobs",
    "logs_url": "https://api.github.com/repos/.../actions/runs/12345678/logs"
  }
}
```

#### `get_workflow_run_jobs`

```python
@mcp.tool()
def get_workflow_run_jobs(run_id: int) -> dict[str, Any]:
    """ワークフロー実行内の全ジョブとステップのステータスを取得する。"""
```

**レスポンス例:**
```json
{
  "ok": true,
  "run_id": 12345678,
  "jobs": [
    {
      "id": 98765432,
      "name": "Deploy backend to ECS",
      "status": "completed",
      "conclusion": "failure",
      "started_at": "2026-03-20T10:00:15Z",
      "completed_at": "2026-03-20T10:05:30Z",
      "steps": [
        {"name": "Checkout", "status": "completed", "conclusion": "success", "number": 1},
        {"name": "Configure AWS credentials", "status": "completed", "conclusion": "success", "number": 2},
        {"name": "Build and push backend image", "status": "completed", "conclusion": "failure", "number": 6}
      ]
    }
  ]
}
```

#### `get_job_logs`

```python
@mcp.tool()
def get_job_logs(job_id: int, tail_lines: int = 100) -> dict[str, Any]:
    """
    特定ジョブのログを取得する。
    tail_lines でログ末尾の行数を制限（デフォルト100行、最大500行）。
    失敗ステップのログ調査に最適。
    """
```

**レスポンス例:**
```json
{
  "ok": true,
  "job_id": 98765432,
  "job_name": "Deploy backend to ECS",
  "log_lines": 87,
  "truncated": false,
  "logs": "2026-03-20T10:03:12Z ##[group]Build and push backend image\n2026-03-20T10:05:28Z ERROR: failed to solve: ...\n..."
}
```

#### `list_workflows`

```python
@mcp.tool()
def list_workflows() -> dict[str, Any]:
    """リポジトリに定義されているワークフローの一覧を取得する。"""
```

**レスポンス例:**
```json
{
  "ok": true,
  "repository": "matthewTechCom/todo_sample",
  "workflows": [
    {"id": 111, "name": "Deploy Backend", "path": ".github/workflows/deploy-backend.yml", "state": "active"},
    {"id": 222, "name": "Deploy Frontend", "path": ".github/workflows/deploy-frontend.yml", "state": "active"}
  ]
}
```

---

## ユースケース

```mermaid
sequenceDiagram
    actor Dev as 開発者
    participant IDE as MCP Client
    participant GW as AgentCore Gateway
    participant RT as GHA MCP Runtime
    participant GH as GitHub API

    Dev->>IDE: "直近のデプロイ失敗を調べて"

    IDE->>GW: ghactions___list_workflow_runs(status="failure", limit=5)
    GW->>RT: Forward
    RT->>GH: GET /repos/.../actions/runs?status=failure&per_page=5
    GH-->>RT: Runs JSON
    RT-->>IDE: 失敗したRun一覧

    IDE->>GW: ghactions___get_workflow_run_jobs(run_id=12345678)
    GW->>RT: Forward
    RT->>GH: GET /repos/.../actions/runs/12345678/jobs
    GH-->>RT: Jobs JSON
    RT-->>IDE: ジョブ一覧（Step 6 "Build and push" が failure）

    IDE->>GW: ghactions___get_job_logs(job_id=98765432, tail_lines=50)
    GW->>RT: Forward
    RT->>GH: GET /repos/.../actions/jobs/98765432/logs
    GH-->>RT: ログテキスト
    RT-->>IDE: ログ末尾50行

    IDE-->>Dev: "Deploy Backend の Run #12345678 が失敗。<br/>Step 6 'Build and push backend image' で<br/>Dockerfile.prod のマルチステージビルドエラー。<br/>具体的には..."
```

### 他MCPとの連携シナリオ

```mermaid
graph TB
    subgraph "障害調査フロー"
        Q["開発者: デプロイ後にエラーが出ている"]

        S1["1. ghactions___list_workflow_runs<br/>→ 直近のデプロイ状態確認"]
        S2["2. cwlogs___query_cloudwatch_insights<br/>→ ECSアプリログでエラー検索"]
        S3["3. rdsquery___query_rds<br/>→ DBマイグレーション状態確認"]
        S4["4. ghactions___get_job_logs<br/>→ デプロイステップのログ確認"]

        Q --> S1
        S1 --> S2
        S2 --> S3
        S3 --> S4
    end

    style S1 fill:#f9e79f,stroke:#f39c12
    style S2 fill:#aed6f1,stroke:#2980b9
    style S3 fill:#d5f5e3,stroke:#27ae60
    style S4 fill:#f9e79f,stroke:#f39c12
```

---

## セキュリティ設計

### 多層防御

```mermaid
graph TB
    subgraph "Layer 1: Gateway認証"
        L1["AgentCore Gateway<br/>Cognito JWT / None (demo)"]
    end

    subgraph "Layer 2: Runtime認証"
        L2["CustomJWTAuthorizer<br/>OAuth scope: invoke"]
    end

    subgraph "Layer 3: アプリケーション制御"
        L3A["読み取り専用API<br/>GET リクエストのみ"]
        L3B["リポジトリ許可リスト<br/>ALLOWED_REPOSITORIES"]
        L3C["ログ行数制限<br/>最大500行"]
        L3D["レート制限<br/>GitHub API制限を尊重"]
    end

    subgraph "Layer 4: GitHub PAT制御"
        L4A["Fine-grained PAT<br/>最小スコープ"]
        L4B["対象リポジトリ限定"]
        L4C["有効期限設定"]
    end

    subgraph "Layer 5: Secrets Manager"
        L5["PAT暗号化保存<br/>IAMロール最小権限"]
    end

    L1 --> L2 --> L3A
    L3A --> L3B --> L3C --> L3D
    L3D --> L4A --> L4B --> L4C
    L4C --> L5

    style L3A fill:#fadbd8,stroke:#e74c3c
    style L3B fill:#fadbd8,stroke:#e74c3c
    style L4A fill:#fadbd8,stroke:#e74c3c
    style L4B fill:#fadbd8,stroke:#e74c3c
```

### GitHub API レート制限

| 認証方式 | 制限 | 備考 |
|---|---|---|
| PAT (authenticated) | 5,000 req/hour | 十分 |
| 未認証 | 60 req/hour | 不十分 |

MCP Serverは毎回のツール呼び出しでAPIリクエストを送るため、PATによる認証は必須。

---

## ディレクトリ構成

```
devops_agent/
├── mcp_server.py                          # 既存: CloudWatch MCP Server
├── rds_mcp_server.py                      # 既存: RDS MCP Server
├── gha_mcp_server.py                      # 新規: GitHub Actions MCP Server
├── Dockerfile                             # 既存: CloudWatch用
├── Dockerfile.rds                         # 既存: RDS用
├── Dockerfile.gha                         # 新規: GitHub Actions用
├── requirements.txt                       # 既存
├── requirements-rds.txt                   # 既存
├── requirements-gha.txt                   # 新規
└── terraform/
    ├── # 既存ファイル（変更あり）
    ├── locals.tf                          # 更新: GHA用ローカル変数追加
    ├── variables.tf                       # 更新: GHA用変数追加
    ├── outputs.tf                         # 更新: GHA用出力追加
    ├── # 新規ファイル
    ├── gha_runtime.tf                     # 新規: Runtime + ECR + IAM + Secrets
    ├── gha_gateway_target.tf              # 新規: Gateway Target追加
    └── templates/
        ├── gha_runtime.yaml.tftpl         # 新規: Runtime CFn テンプレート
        └── gha_gateway_target.yaml.tftpl  # 新規: Gateway Target CFn テンプレート
```

---

## Terraform リソース追加一覧

```mermaid
graph TB
    subgraph "新規 Terraform リソース"
        subgraph "Secrets"
            SM_PAT["aws_secretsmanager_secret<br/>github_pat"]
            SM_CFG["aws_secretsmanager_secret<br/>gha_runtime_config"]
        end

        subgraph "AgentCore Runtime"
            ECR["aws_ecr_repository<br/>gha_runtime"]
            IAM_RT["aws_iam_role + policy<br/>gha_runtime"]
            CF_RT["aws_cloudformation_stack<br/>gha_runtime"]
        end

        subgraph "AgentCore Gateway Target"
            CF_GT["aws_cloudformation_stack<br/>gha_gateway_target"]
        end
    end

    CF_RT --> ECR
    CF_RT --> SM_CFG
    CF_RT --> IAM_RT
    CF_GT --> CF_RT

    style SM_PAT fill:#fadbd8,stroke:#e74c3c
    style CF_RT fill:#f9e79f,stroke:#f39c12
    style CF_GT fill:#d5f5e3,stroke:#27ae60
```

### RDS MCP との比較（シンプルさ）

| 項目 | RDS MCP | GitHub Actions MCP |
|---|---|---|
| ネットワーク | Lambda Proxy 必要 | 直接HTTPS（Lambda不要） |
| VPC | todo_sample VPC内に配置 | VPC不要 |
| Security Group | Lambda SG + RDS SG ルール | なし |
| DB認証 | Secrets Manager (DATABASE_URL) | Secrets Manager (GitHub PAT) |
| 追加AWSリソース | Lambda + SG + SG Rules | なし |
| Terraform 新規ファイル | 3ファイル | 2ファイル |

GitHub Actions MCPはLambda Proxyが不要な分、**RDS MCPよりシンプルな構成**になる。

---

## 設定値一覧

### 環境変数 (GHA MCP Server)

| 変数名 | 説明 | デフォルト |
|---|---|---|
| `GITHUB_PAT_SECRET_ID` | GitHub PATのSecrets Manager ARN | (必須) |
| `GITHUB_REPOSITORY` | 対象リポジトリ（owner/repo形式） | (必須) |
| `ALLOWED_REPOSITORIES` | 許可リポジトリのCSV | (GITHUB_REPOSITORYのみ) |
| `LOG_TAIL_MAX_LINES` | ログ取得の最大行数 | `500` |
| `RUNTIME_CONFIG_SECRET_ID` | Runtime設定のSecrets Manager ARN | (必須) |

### Terraform 変数

| 変数名 | 説明 | デフォルト |
|---|---|---|
| `gha_runtime_image_tag` | コンテナイメージタグ | `latest` |
| `github_pat_secret_arn` | GitHub PATのSecrets Manager ARN（手動作成） | `""` |
| `github_repository` | 対象リポジトリ | `matthewTechCom/todo_sample` |
| `gha_allowed_repositories` | 許可リポジトリリスト | `null`（= github_repositoryのみ） |

---

## 実装ステップ

```mermaid
gantt
    title GitHub Actions MCP Server 実装計画
    dateFormat YYYY-MM-DD
    section Phase 1: 準備
        GitHub Fine-grained PAT 作成                :p1a, 2026-03-21, 1d
        Secrets Manager に PAT 保存                  :p1b, after p1a, 1d
    section Phase 2: MCP Server
        gha_mcp_server.py 実装                      :p2a, after p1b, 1d
        Dockerfile.gha + requirements 作成           :p2b, after p2a, 1d
    section Phase 3: Terraform
        gha_runtime.tf + CFn テンプレート             :p3a, after p2b, 1d
        gha_gateway_target.tf                        :p3b, after p3a, 1d
        locals / variables / outputs 更新             :p3c, after p3b, 1d
    section Phase 4: デプロイ
        ECR push + terraform apply                   :p4a, after p3c, 1d
    section Phase 5: テスト
        統合テスト (MCP Client → Gateway → GitHub)   :p5a, after p4a, 1d
```

---

## データフロー詳細

```mermaid
sequenceDiagram
    actor User as 開発者
    participant IDE as MCP Client
    participant GW as AgentCore Gateway
    participant Cognito as Cognito
    participant RT as GHA MCP Runtime
    participant SM as Secrets Manager
    participant GH as GitHub API

    User->>IDE: "backendのデプロイが失敗した原因を調べて"
    IDE->>GW: POST /mcp (tool: ghactions___list_workflow_runs)

    Note over GW,Cognito: OAuth2 認証フロー
    GW->>Cognito: POST /oauth2/token (client_credentials)
    Cognito-->>GW: access_token (JWT)

    GW->>RT: MCP tool call + Bearer token
    Note over RT: JWT検証 (CustomJWTAuthorizer)

    RT->>SM: GetSecretValue(github-pat-secret)
    SM-->>RT: ghp_xxxxxxxxxxxxx

    RT->>GH: GET /repos/matthewTechCom/todo_sample/actions/runs<br/>?status=failure&per_page=5<br/>Authorization: Bearer ghp_xxx
    GH-->>RT: {"workflow_runs": [...]}

    RT-->>GW: MCP tool result (JSON)
    GW-->>IDE: 結果表示
    IDE-->>User: "直近の失敗は Run #12345678 (Deploy Backend)..."

    User->>IDE: "そのRunのログを見せて"
    IDE->>GW: ghactions___get_workflow_run_jobs(run_id=12345678)
    GW->>RT: Forward
    RT->>GH: GET /repos/.../actions/runs/12345678/jobs
    GH-->>RT: Jobs with steps
    RT-->>IDE: ジョブ・ステップ一覧

    IDE->>GW: ghactions___get_job_logs(job_id=98765432)
    GW->>RT: Forward
    RT->>GH: GET /repos/.../actions/jobs/98765432/logs
    GH-->>RT: ログテキスト (plain text)
    RT->>RT: tail_lines で末尾切り出し
    RT-->>IDE: ログ内容
    IDE-->>User: "Step 6 'Build and push' で Dockerfile エラー..."
```

---

## 既存MCPサーバーとの比較

| 項目 | CloudWatch MCP | RDS MCP | GitHub Actions MCP (新規) |
|---|---|---|---|
| データソース | CloudWatch Logs | RDS PostgreSQL | GitHub REST API |
| 接続方式 | boto3 (IAM) | Lambda Proxy → psycopg2 | httpx + PAT |
| ネットワーク | Public API | Lambda (VPC) → Private RDS | Public API |
| 認証 | IAMロール | IAMロール + DB認証 | GitHub PAT |
| Lambda | 不要 | 必要 | 不要 |
| VPC | 不要 | 必要 | 不要 |
| Target名 | `cwlogs` | `rdsquery` | `ghactions` |
| ツール数 | 1 | 3 | 5 |
| Container | `Dockerfile` | `Dockerfile.rds` | `Dockerfile.gha` |
| 追加依存 | boto3 | boto3 | httpx, boto3 |
| 複雑度 | 低 | 高（Lambda+VPC+SG） | **低** |
