# RDS MCP Server 設計書

## 概要

todoアプリのRDS（PostgreSQL）に対してAgent CoreからSQLクエリを実行し、データ調査を行えるMCPサーバーを新規作成する。既存のCloudWatch MCP Server（`devops_agent/mcp_server.py`）と同じアーキテクチャパターンに従い、AgentCore Gateway上に新しいTargetとして追加する。

---

## 現状構成

```mermaid
graph LR
    Client["MCP Client<br/>(Codex / VS Code / Copilot)"]
    GW["AgentCore Gateway"]
    RT_CW["AgentCore Runtime<br/>CloudWatch MCP Server"]
    CW["CloudWatch Logs<br/>Insights"]

    Client -->|MCP over HTTP| GW
    GW -->|OAuth2 + MCP| RT_CW
    RT_CW -->|boto3 IAM| CW
```

## 目標構成

```mermaid
graph LR
    Client["MCP Client<br/>(Codex / VS Code / Copilot)"]
    GW["AgentCore Gateway"]
    RT_CW["Runtime: CloudWatch<br/>MCP Server"]
    RT_RDS["Runtime: RDS Query<br/>MCP Server"]
    CW["CloudWatch Logs"]
    RDS["RDS PostgreSQL<br/>(todo_sample)"]
    SM["Secrets Manager<br/>(DATABASE_URL)"]

    Client -->|MCP over HTTP| GW
    GW -->|Target: cwlogs| RT_CW
    GW -->|Target: rdsquery| RT_RDS
    RT_CW -->|boto3| CW
    RT_RDS -->|boto3| SM
    RT_RDS -->|psycopg2| RDS

    style RT_RDS fill:#f9e79f,stroke:#f39c12,stroke-width:2px
    style RDS fill:#aed6f1,stroke:#2980b9,stroke-width:2px
```

---

## アーキテクチャ詳細

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

        subgraph "Target: rdsquery （新規）"
            RT2["Runtime<br/>rds_mcp_server.py"]
        end
    end

    subgraph "AWS - Auth"
        Cognito["Cognito User Pool"]
        OAuth["AgentCore Identity<br/>OAuth Provider"]
    end

    subgraph "AWS - Data Sources"
        CW["CloudWatch Logs"]
        SM_DB["Secrets Manager<br/>DATABASE_URL"]
        RDS["RDS PostgreSQL<br/>VPC Private Subnet"]
    end

    IDE -->|Streamable HTTP| GW
    GW -->|OAuth2 client_credentials| Cognito
    Cognito --> OAuth
    GW -->|cwlogs___query_cloudwatch_insights| RT1
    GW -->|rdsquery___query_rds<br/>rdsquery___list_tables<br/>rdsquery___describe_table| RT2

    RT1 -->|StartQuery / GetQueryResults| CW
    RT2 -->|GetSecretValue| SM_DB
    RT2 -->|PostgreSQL 5432| RDS

    style RT2 fill:#f9e79f,stroke:#f39c12,stroke-width:2px
```

---

## ネットワーク構成

```mermaid
graph TB
    subgraph "VPC (todo_sample)"
        subgraph "Private DB Subnet"
            RDS["RDS PostgreSQL<br/>:5432"]
        end

        subgraph "Private App Subnet"
            ECS["ECS Backend<br/>(Rails)"]
        end

        SG_DB["Security Group: db-sg<br/>Inbound: 5432 from ecs-sg"]
        SG_ECS["Security Group: ecs-sg"]
    end

    subgraph "AgentCore (AWS Managed)"
        RT_RDS["RDS MCP Runtime"]
    end

    RT_RDS -.->|"❌ 直接接続不可<br/>(AgentCore = Public Network)"| RDS

    ECS -->|5432| RDS

    style RT_RDS fill:#f9e79f,stroke:#f39c12
```

### ネットワーク課題と解決策

AgentCore RuntimeはPublicネットワークモードで動作するため、**VPC内のPrivate RDSに直接接続できない**。以下の3つのアプローチを検討する。

```mermaid
graph TB
    subgraph "案1: RDS Data API 経由（推奨）"
        RT1["RDS MCP Runtime"]
        DataAPI["RDS Data API<br/>(boto3)"]
        RDS1["RDS PostgreSQL"]
        RT1 -->|boto3 rds-data| DataAPI
        DataAPI --> RDS1
    end

    subgraph "案2: RDS Proxy + Public Endpoint"
        RT2["RDS MCP Runtime"]
        Proxy["RDS Proxy<br/>(Public Endpoint)"]
        RDS2["RDS PostgreSQL"]
        RT2 -->|TLS + IAM Auth| Proxy
        Proxy --> RDS2
    end

    subgraph "案3: Lambda Proxy 経由"
        RT3["RDS MCP Runtime"]
        Lambda["Lambda Function<br/>(VPC内)"]
        RDS3["RDS PostgreSQL"]
        RT3 -->|boto3 lambda.invoke| Lambda
        Lambda -->|psycopg2| RDS3
    end

    style RT1 fill:#d5f5e3,stroke:#27ae60,stroke-width:2px
    style DataAPI fill:#d5f5e3,stroke:#27ae60,stroke-width:2px
```

| 案 | メリット | デメリット | 推奨度 |
|---|---|---|---|
| **案1: RDS Data API** | IAM認証のみ、VPC不要、boto3で完結 | Aurora限定（標準RDSは非対応）、RDS再構築が必要 | ⚠️ Auroraへの移行が必要 |
| **案2: RDS Proxy (Public)** | 既存RDSをそのまま利用可能、IAM認証対応 | RDS Proxyの追加コスト、Public Endpoint設定 | ✅ 推奨 |
| **案3: Lambda Proxy** | 既存RDSをそのまま利用、VPC内から接続 | Lambda追加、レイテンシ増加、Cold Start | ✅ 推奨（シンプル） |

---

## 推奨案: Lambda Proxy パターン

既存のRDS構成を変更せず、最もシンプルに実装できる **案3: Lambda Proxy** を推奨する。

### 全体フロー

```mermaid
sequenceDiagram
    participant Client as MCP Client
    participant GW as AgentCore Gateway
    participant RT as RDS MCP Runtime
    participant SM as Secrets Manager
    participant LMD as Lambda (VPC内)
    participant RDS as RDS PostgreSQL

    Client->>GW: MCP tool call (rdsquery___query_rds)
    GW->>RT: Forward (OAuth2)
    RT->>LMD: lambda.invoke(query, params)
    LMD->>SM: GetSecretValue(DATABASE_URL)
    LMD->>RDS: SQL実行 (psycopg2)
    RDS-->>LMD: 結果セット
    LMD-->>RT: JSON レスポンス
    RT-->>GW: MCP tool result
    GW-->>Client: 結果表示
```

### Lambda 設計

```mermaid
graph TB
    subgraph "Lambda Function (rds-query-proxy)"
        Handler["lambda_handler()"]
        Validate["入力バリデーション"]
        Connect["DB接続<br/>(psycopg2)"]
        Execute["SQL実行"]
        Format["結果フォーマット"]

        Handler --> Validate
        Validate --> Connect
        Connect --> Execute
        Execute --> Format
    end

    subgraph "VPC Configuration"
        PrivSub["Private App Subnet"]
        SG_Lambda["Security Group: lambda-sg"]
    end

    subgraph "セキュリティ"
        ReadOnly["READ ONLY制約<br/>SET TRANSACTION READ ONLY"]
        Timeout["クエリタイムアウト<br/>statement_timeout=30s"]
        RowLimit["行数制限<br/>MAX 1000 rows"]
        Allowlist["SQL許可リスト<br/>SELECT, EXPLAIN, SHOW"]
    end

    Handler -.-> ReadOnly
    Handler -.-> Timeout
    Handler -.-> RowLimit
    Handler -.-> Allowlist

    style ReadOnly fill:#fadbd8,stroke:#e74c3c
    style Timeout fill:#fadbd8,stroke:#e74c3c
    style RowLimit fill:#fadbd8,stroke:#e74c3c
    style Allowlist fill:#fadbd8,stroke:#e74c3c
```

---

## MCP Server 設計

### 提供ツール一覧

| ツール名 | 説明 | パラメータ |
|---|---|---|
| `query_rds` | SQLクエリを実行して結果を返す | `query: str`, `params: list` (optional), `max_rows: int` (default: 100) |
| `list_tables` | データベース内のテーブル一覧を返す | なし |
| `describe_table` | テーブルのスキーマ情報を返す | `table_name: str` |

### ツール詳細

#### `query_rds`

```python
@mcp.tool()
async def query_rds(query: str, params: list = None, max_rows: int = 100) -> str:
    """
    RDS PostgreSQL に対して読み取り専用のSQLクエリを実行する。
    SELECT文のみ許可。max_rowsで返却行数を制限（最大1000）。
    """
```

**レスポンス例:**
```json
{
  "ok": true,
  "query": "SELECT id, title, completed FROM todos WHERE completed = true LIMIT 10",
  "row_count": 3,
  "columns": ["id", "title", "completed"],
  "rows": [
    {"id": 1, "title": "タスク1", "completed": true},
    {"id": 2, "title": "タスク2", "completed": true},
    {"id": 3, "title": "タスク3", "completed": true}
  ],
  "execution_time_ms": 12,
  "truncated": false
}
```

#### `list_tables`

```python
@mcp.tool()
async def list_tables() -> str:
    """データベース内の全テーブルとレコード数を一覧表示する。"""
```

**レスポンス例:**
```json
{
  "ok": true,
  "tables": [
    {"table_name": "todos", "row_count": 42},
    {"table_name": "schema_migrations", "row_count": 5},
    {"table_name": "ar_internal_metadata", "row_count": 1}
  ]
}
```

#### `describe_table`

```python
@mcp.tool()
async def describe_table(table_name: str) -> str:
    """テーブルのカラム定義、インデックス、制約を返す。"""
```

**レスポンス例:**
```json
{
  "ok": true,
  "table_name": "todos",
  "columns": [
    {"name": "id", "type": "bigint", "nullable": false, "default": "nextval('todos_id_seq')"},
    {"name": "title", "type": "character varying", "nullable": false, "default": null},
    {"name": "completed", "type": "boolean", "nullable": false, "default": "false"},
    {"name": "created_at", "type": "timestamp(6)", "nullable": false, "default": null},
    {"name": "updated_at", "type": "timestamp(6)", "nullable": false, "default": null}
  ],
  "indexes": [
    {"name": "todos_pkey", "columns": ["id"], "unique": true}
  ]
}
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
        L3A["SQLホワイトリスト<br/>SELECT / EXPLAIN のみ"]
        L3B["行数制限<br/>最大1000行"]
        L3C["タイムアウト<br/>30秒"]
        L3D["パラメータバインド<br/>SQLインジェクション防止"]
    end

    subgraph "Layer 4: Lambda制御"
        L4A["READ ONLY トランザクション"]
        L4B["VPC内実行<br/>Private Subnet"]
        L4C["IAMロール最小権限"]
    end

    subgraph "Layer 5: RDS制御"
        L5A["Security Group<br/>Lambda SG からのみ許可"]
        L5B["専用DBユーザー (読取専用)"]
    end

    L1 --> L2 --> L3A
    L3A --> L3B --> L3C --> L3D
    L3D --> L4A --> L4B --> L4C
    L4C --> L5A --> L5B

    style L3A fill:#fadbd8,stroke:#e74c3c
    style L3D fill:#fadbd8,stroke:#e74c3c
    style L4A fill:#fadbd8,stroke:#e74c3c
    style L5B fill:#fadbd8,stroke:#e74c3c
```

### 読み取り専用DBユーザー

```sql
-- Lambda用の読み取り専用ユーザーを作成
CREATE USER rds_mcp_readonly WITH PASSWORD '...';
GRANT CONNECT ON DATABASE backend_production TO rds_mcp_readonly;
GRANT USAGE ON SCHEMA public TO rds_mcp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO rds_mcp_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO rds_mcp_readonly;
```

---

## ディレクトリ構成

```
devops_agent/
├── mcp_server.py                          # 既存: CloudWatch MCP Server
├── rds_mcp_server.py                      # 新規: RDS MCP Server
├── rds_lambda/                            # 新規: Lambda Proxy
│   ├── lambda_handler.py                  #   Lambda関数本体
│   └── requirements.txt                   #   psycopg2-binary, boto3
├── Dockerfile                             # 既存: CloudWatch用
├── Dockerfile.rds                         # 新規: RDS MCP Server用
├── requirements.txt                       # 既存
├── requirements-rds.txt                   # 新規: RDS MCP Server依存
├── mcp.json                               # 更新: rdsquery target追加
└── terraform/
    ├── # 既存ファイル (変更なし)
    ├── variables.tf
    ├── locals.tf
    ├── iam.tf                             # 更新: RDS用IAMポリシー追加
    ├── cognito.tf                         # 更新: RDS用スコープ追加（共有可）
    ├── ecr.tf                             # 更新: RDS用リポジトリ追加
    ├── # 新規ファイル
    ├── rds_lambda.tf                      # 新規: Lambda + SG + IAM
    ├── rds_runtime.tf                     # 新規: AgentCore Runtime (RDS)
    ├── rds_gateway_target.tf              # 新規: Gateway Target追加
    ├── rds_runtime_config.tf              # 新規: Secrets Manager設定
    └── templates/
        ├── runtime.yaml.tftpl             # 既存
        ├── rds_runtime.yaml.tftpl         # 新規: RDS Runtime CF テンプレート
        └── gateway_target_rds.yaml.tftpl  # 新規: RDS Target CF テンプレート
```

---

## Terraform リソース追加一覧

```mermaid
graph TB
    subgraph "新規 Terraform リソース"
        subgraph "Lambda"
            LF["aws_lambda_function<br/>rds_query_proxy"]
            LR["aws_iam_role<br/>lambda_rds_proxy"]
            LP["aws_iam_role_policy<br/>lambda_rds_secrets"]
            SG_L["aws_security_group<br/>lambda_rds"]
        end

        subgraph "AgentCore Runtime"
            ECR2["aws_ecr_repository<br/>rds_runtime"]
            SM2["aws_secretsmanager_secret<br/>rds_runtime_config"]
            CF_RT["aws_cloudformation_stack<br/>rds_runtime"]
            IAM_RT["aws_iam_role_policy<br/>rds_runtime"]
        end

        subgraph "AgentCore Gateway Target"
            CF_GT["aws_cloudformation_stack<br/>rds_gateway_target"]
        end

        subgraph "RDS Security"
            SG_RULE["aws_security_group_rule<br/>db_from_lambda"]
            DB_USER["読取専用ユーザー<br/>(手動 or null_resource)"]
            SM_RO["aws_secretsmanager_secret<br/>rds_readonly_url"]
        end
    end

    LF --> LR
    LF --> SG_L
    LR --> LP
    SG_L --> SG_RULE
    CF_RT --> ECR2
    CF_RT --> SM2
    CF_RT --> IAM_RT
    CF_GT --> CF_RT

    style LF fill:#aed6f1,stroke:#2980b9
    style CF_RT fill:#f9e79f,stroke:#f39c12
    style CF_GT fill:#d5f5e3,stroke:#27ae60
```

### 既存リソースへの変更

| ファイル | 変更内容 |
|---|---|
| `iam.tf` | RDS Runtime用IAMロール追加、Lambda invoke権限 |
| `cognito.tf` | RDS Runtime用スコープ追加（または既存スコープを共有） |
| `ecr.tf` | RDS Runtime用ECRリポジトリ追加 |
| `security_and_data.tf` (todo_sample側) | DB Security GroupにLambda SGからのIngressルール追加 |

---

## 実装ステップ

```mermaid
gantt
    title RDS MCP Server 実装計画
    dateFormat YYYY-MM-DD
    section Phase 1: Lambda Proxy
        Lambda関数実装 (lambda_handler.py)       :p1a, 2026-03-20, 1d
        読取専用DBユーザー作成                     :p1b, 2026-03-20, 1d
        Lambda Terraform定義                      :p1c, after p1a, 1d
        SG / IAM / Secrets設定                    :p1d, after p1c, 1d
    section Phase 2: MCP Server
        rds_mcp_server.py 実装                    :p2a, after p1d, 1d
        Dockerfile.rds 作成                       :p2b, after p2a, 1d
    section Phase 3: AgentCore
        Runtime Terraform + CFn テンプレート       :p3a, after p2b, 1d
        Gateway Target追加                        :p3b, after p3a, 1d
        ECR push + deploy                         :p3c, after p3b, 1d
    section Phase 4: テスト
        ローカルテスト (Lambda単体)                :p4a, after p1d, 1d
        統合テスト (MCP Client → Gateway → RDS)   :p4b, after p3c, 2d
```

---

## データフロー詳細

```mermaid
sequenceDiagram
    actor User as 開発者
    participant IDE as MCP Client
    participant GW as AgentCore Gateway
    participant Cognito as Cognito
    participant RT as RDS MCP Runtime
    participant Lambda as Lambda (VPC内)
    participant SM as Secrets Manager
    participant RDS as PostgreSQL

    User->>IDE: "todosテーブルの未完了タスクを見せて"
    IDE->>GW: POST /mcp (tool: rdsquery___query_rds)

    Note over GW,Cognito: OAuth2 認証フロー
    GW->>Cognito: POST /oauth2/token (client_credentials)
    Cognito-->>GW: access_token (JWT)

    GW->>RT: MCP tool call + Bearer token
    Note over RT: JWT検証 (CustomJWTAuthorizer)

    RT->>RT: SQLバリデーション<br/>SELECT文のみ許可

    RT->>Lambda: boto3 lambda.invoke({<br/>  "action": "query",<br/>  "sql": "SELECT * FROM todos WHERE completed = false",<br/>  "max_rows": 100<br/>})

    Lambda->>SM: GetSecretValue(readonly_db_url)
    SM-->>Lambda: postgresql://rds_mcp_readonly:***@host:5432/db

    Lambda->>Lambda: SET TRANSACTION READ ONLY<br/>SET statement_timeout = '30s'
    Lambda->>RDS: SELECT * FROM todos WHERE completed = false
    RDS-->>Lambda: ResultSet (rows)

    Lambda-->>RT: {"ok": true, "rows": [...], "row_count": 15}
    RT-->>GW: MCP tool result (JSON)
    GW-->>IDE: 結果表示
    IDE-->>User: "未完了タスクが15件あります：..."
```

---

## 設定値一覧

### 環境変数 (RDS MCP Server)

| 変数名 | 説明 | デフォルト |
|---|---|---|
| `RDS_LAMBDA_FUNCTION_NAME` | Lambda Proxy関数名 | (必須) |
| `QUERY_MAX_ROWS` | 最大返却行数 | `1000` |
| `QUERY_TIMEOUT_SECONDS` | Lambda呼び出しタイムアウト | `45` |
| `RUNTIME_CONFIG_SECRET_ID` | Secrets Manager ARN | (必須) |

### 環境変数 (Lambda)

| 変数名 | 説明 | デフォルト |
|---|---|---|
| `DB_SECRET_ARN` | 読取専用DB接続文字列のSecret ARN | (必須) |
| `STATEMENT_TIMEOUT_MS` | SQLタイムアウト (ms) | `30000` |
| `MAX_ROWS` | 最大行数ハードリミット | `1000` |

---

## 既存CloudWatch MCPとの比較

| 項目 | CloudWatch MCP | RDS MCP (新規) |
|---|---|---|
| データソース | CloudWatch Logs | RDS PostgreSQL |
| 接続方式 | boto3 (IAM) | Lambda Proxy → psycopg2 |
| ネットワーク | Public API | Lambda (VPC) → Private RDS |
| 認証 | IAMロール | IAMロール + DB認証 |
| Target名 | `cwlogs` | `rdsquery` |
| ツール数 | 1 | 3 |
| 読取専用保証 | CloudWatch API自体が読取 | アプリ + DB + トランザクション |
| Container | `Dockerfile` | `Dockerfile.rds` |
| ECR | 既存リポジトリ | 新規リポジトリ |
