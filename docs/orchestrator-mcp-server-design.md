# Orchestrator MCP Server 設計書

## 概要

自然言語による障害調査指示を受け取り、Bedrock Claude（ReActパターン）で既存のMCPツール群を自動的に呼び分けながらエラー原因を特定し、Markdownレポートとして出力するオーケストレーターMCPサーバーを新規作成する。Cursor等のIDEから「直近のCIが失敗した原因を調べて」と入力するだけで、CloudWatch・RDS・GitHub Actionsを横断した調査が自動実行される。

---

## 現状構成

```mermaid
graph LR
    Client["MCP Client<br/>(Cursor / VS Code / Copilot)"]
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
    RT_GHA --> GH
```

**課題**: クライアント（Cursor）側のLLMがどのツールを呼ぶか判断しており、複数ツールを横断した調査はユーザーが手動で指示する必要がある。

## 目標構成

```mermaid
graph LR
    Client["MCP Client<br/>(Cursor / VS Code / Copilot)"]
    GW["AgentCore Gateway"]
    RT_ORCH["Target: orchestrator<br/>Orchestrator MCP"]
    RT_CW["Target: cwlogs<br/>CloudWatch MCP"]
    RT_RDS["Target: rdsquery<br/>RDS Query MCP"]
    RT_GHA["Target: ghactions<br/>GitHub Actions MCP"]
    Bedrock["Bedrock Claude<br/>(Sonnet 4)"]
    CW["CloudWatch Logs"]
    RDS["RDS PostgreSQL"]
    GH["GitHub API"]

    Client -->|"自然言語で指示"| GW
    GW --> RT_ORCH
    RT_ORCH -->|"ReAct推論"| Bedrock
    RT_ORCH -->|"ツール呼び出し"| GW
    GW --> RT_CW
    GW --> RT_RDS
    GW --> RT_GHA
    RT_CW --> CW
    RT_RDS --> RDS
    RT_GHA --> GH

    style RT_ORCH fill:#e8daef,stroke:#8e44ad,stroke-width:2px
    style Bedrock fill:#d5f5e3,stroke:#27ae60,stroke-width:2px
```

**ポイント**: オーケストレーターは自身もGateway上のMCPサーバーでありながら、Gatewayを経由して他のMCPツールをクライアントとして呼び出す。

---

## アーキテクチャ詳細

### 既存MCPサーバーとの構成比較

```mermaid
graph TB
    subgraph "既存パターン: 個別ツール呼び出し"
        Client1["MCP Client<br/>(LLMはクライアント側)"]
        GW1["Gateway"]
        T1["cwlogs"]
        T2["rdsquery"]
        T3["ghactions"]
        Client1 -->|"ユーザーが指示"| GW1
        GW1 --> T1
        GW1 --> T2
        GW1 --> T3
    end

    subgraph "新規パターン: AI駆動オーケストレーション"
        Client2["MCP Client"]
        GW2["Gateway"]
        ORCH["orchestrator<br/>(Bedrock Claude内蔵)"]
        T4["cwlogs"]
        T5["rdsquery"]
        T6["ghactions"]
        Client2 -->|"自然言語1回"| GW2
        GW2 --> ORCH
        ORCH -->|"ReActループ"| GW2
        GW2 --> T4
        GW2 --> T5
        GW2 --> T6
    end

    style ORCH fill:#e8daef,stroke:#8e44ad,stroke-width:2px
```

**オーケストレーターはサーバーサイドでLLMを実行**するため、クライアント（Cursor）は1回の自然言語入力だけで複雑な横断調査が完了する。

### コンポーネント図

```mermaid
graph TB
    subgraph "MCP Client"
        IDE["Cursor / VS Code / Copilot"]
    end

    subgraph "AWS - AgentCore"
        GW["AgentCore Gateway<br/>Protocol: MCP"]

        subgraph "Target: cwlogs"
            RT1["Runtime<br/>mcp_server.py"]
        end

        subgraph "Target: rdsquery"
            RT2["Runtime<br/>rds_mcp_server.py"]
        end

        subgraph "Target: ghactions"
            RT3["Runtime<br/>gha_mcp_server.py"]
        end

        subgraph "Target: orchestrator（新規）"
            RT4["Runtime<br/>orchestrator_mcp_server.py"]
        end
    end

    subgraph "AWS - AI"
        Bedrock["Amazon Bedrock<br/>Claude Sonnet 4<br/>(Converse API)"]
    end

    subgraph "AWS - Data Sources"
        CW["CloudWatch Logs"]
        RDS["RDS PostgreSQL"]
    end

    subgraph "External"
        GH["GitHub REST API"]
    end

    IDE -->|Streamable HTTP + SigV4| GW
    GW -->|cwlogs___*| RT1
    GW -->|rdsquery___*| RT2
    GW -->|ghactions___*| RT3
    GW -->|orchestrator___*| RT4

    RT4 -->|"Converse API<br/>(tool_use)"| Bedrock
    RT4 -->|"JSON-RPC<br/>tools/call"| GW

    RT1 --> CW
    RT2 --> RDS
    RT3 --> GH

    style RT4 fill:#e8daef,stroke:#8e44ad,stroke-width:2px
    style Bedrock fill:#d5f5e3,stroke:#27ae60,stroke-width:2px
```

---

## ReAct ループ設計

### ReAct パターンとは

ReAct（Reason + Act）は、LLMが「考える → ツールを呼ぶ → 結果を見る → 次の行動を決める」を繰り返すエージェントパターン。

```mermaid
graph LR
    A["自然言語入力"] --> B["Bedrock Claude<br/>推論"]
    B -->|"tool_use"| C["MCP ツール実行"]
    C --> D["結果をLLMに返却"]
    D --> B
    B -->|"end_turn"| E["Markdown レポート出力"]

    style B fill:#e8daef,stroke:#8e44ad
    style E fill:#d5f5e3,stroke:#27ae60
```

### ループ詳細

```mermaid
sequenceDiagram
    participant User as 開発者
    participant IDE as MCP Client
    participant GW as AgentCore Gateway
    participant ORCH as Orchestrator Runtime
    participant Bedrock as Bedrock Claude
    participant SubTool as 既存MCP Tools<br/>(via Gateway)

    User->>IDE: "直近のCIが失敗した原因を調査して"
    IDE->>GW: orchestrator___investigate_error(query="...")
    GW->>ORCH: Forward

    rect rgb(243, 229, 245)
        Note over ORCH,Bedrock: ReAct ループ開始

        loop 最大20ステップ
            ORCH->>Bedrock: Converse API<br/>(messages + toolConfig)

            alt stop_reason == "tool_use"
                Bedrock-->>ORCH: tool_use: ghactions___list_workflow_runs
                ORCH->>GW: JSON-RPC tools/call
                GW->>SubTool: Forward to ghactions target
                SubTool-->>GW: Tool result
                GW-->>ORCH: SSE response
                ORCH->>ORCH: 結果をtruncate<br/>(最大10,000文字)
                Note over ORCH: 結果をmessagesに追加して次のループへ
            else stop_reason == "end_turn"
                Bedrock-->>ORCH: Markdown レポート
                Note over ORCH: ループ終了
            end
        end
    end

    ORCH-->>GW: {"ok": true, "report": "# Investigation Report\n..."}
    GW-->>IDE: MCP tool result
    IDE-->>User: Markdown レポート表示
```

### Bedrock Converse API の tool_use フロー

```mermaid
graph TB
    subgraph "Bedrock Converse API"
        SYS["System Prompt<br/>調査指示 + ツール説明"]
        MSG["Messages<br/>(会話履歴)"]
        TOOLS["toolConfig<br/>(9つのMCPツール定義)"]
    end

    subgraph "レスポンス判定"
        R1{"stopReason?"}
        R1 -->|"end_turn"| OUT["テキスト抽出<br/>→ Markdownレポート"]
        R1 -->|"tool_use"| EXEC["ツール実行<br/>→ 結果をmessagesに追加"]
        EXEC --> MSG
    end

    SYS --> R1
    MSG --> R1
    TOOLS --> R1

    style OUT fill:#d5f5e3,stroke:#27ae60
    style EXEC fill:#f9e79f,stroke:#f39c12
```

---

## MCP Server 設計

### 提供ツール一覧

```mermaid
graph LR
    subgraph "orchestrator ツール"
        T1["investigate_error<br/>AI駆動エラー調査"]
    end
```

| ツール名 | 説明 | パラメータ |
|---|---|---|
| `investigate_error` | 自然言語でエラーや障害を調査し、Markdownレポートを生成 | `query: str` |

### ツール詳細

#### `investigate_error`

```python
@mcp.tool()
def investigate_error(query: str) -> dict[str, Any]:
    """Investigate a production error or DevOps issue using AI-driven analysis.

    The orchestrator automatically queries CloudWatch logs, RDS database,
    and GitHub Actions to build a comprehensive Markdown investigation report.

    Args:
        query: Natural language description of the error or issue to investigate.
              Examples:
              - "直近のデプロイが失敗した原因を調べて"
              - "backendのECSタスクで500エラーが出ている原因を特定して"
              - "CIが失敗している原因を調査して"
    """
```

**レスポンス例:**
```json
{
  "ok": true,
  "report": "# Investigation Report\n\n## Summary\nCIが失敗した主な原因は...\n\n## Investigation Steps\n1. ...\n\n## Root Cause\n...\n\n## Evidence\n```\nActiveRecord::ConnectionNotEstablished...\n```\n\n## Recommended Actions\n1. ...\n\n---\n*Generated at: 2026-03-20T12:41:11Z by DevOps Investigation Agent*",
  "model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
  "elapsed_seconds": 91.6,
  "queried_at": "2026-03-20T12:41:11.782778+00:00"
}
```

### 利用可能なサブツール（Bedrock Claude が自動選択）

| ツール名 | 所属Target | 説明 |
|---|---|---|
| `cwlogs___query_cloudwatch_insights` | cwlogs | CloudWatch Logs Insightsクエリ実行 |
| `rdsquery___query_rds` | rdsquery | SQL読み取りクエリ実行 |
| `rdsquery___list_tables` | rdsquery | テーブル一覧取得 |
| `rdsquery___describe_table` | rdsquery | テーブルスキーマ取得 |
| `ghactions___list_workflows` | ghactions | ワークフロー定義一覧 |
| `ghactions___list_workflow_runs` | ghactions | ワークフロー実行履歴 |
| `ghactions___get_workflow_run` | ghactions | 実行詳細取得 |
| `ghactions___get_workflow_run_jobs` | ghactions | ジョブ・ステップ一覧 |
| `ghactions___get_job_logs` | ghactions | ジョブログ取得 |

---

## レポート出力フォーマット

Bedrock Claudeは以下の構造のMarkdownレポートを生成する。

```markdown
# Investigation Report

## Summary
（1段落のエグゼクティブサマリー）

## Investigation Steps
（番号付きの調査手順と発見事項）

## Root Cause
（根本原因の明確な説明）

## Evidence
（結論を裏付けるログ・クエリ結果・ジョブ出力）

## Recommended Actions
（問題解決のための具体的な次のステップ）

---
*Generated at: {timestamp} by DevOps Investigation Agent*
```

---

## ユースケース

### ユースケース 1: CI失敗の調査

```mermaid
sequenceDiagram
    actor Dev as 開発者
    participant IDE as Cursor
    participant ORCH as Orchestrator
    participant Bedrock as Bedrock Claude
    participant GHA as GitHub Actions MCP

    Dev->>IDE: "直近のCIが失敗した原因を調査して"
    IDE->>ORCH: investigate_error(query="...")

    ORCH->>Bedrock: Step 1: 推論
    Bedrock-->>ORCH: tool_use: list_workflow_runs(status="failure")
    ORCH->>GHA: list_workflow_runs
    GHA-->>ORCH: 失敗Run一覧

    ORCH->>Bedrock: Step 2: 推論
    Bedrock-->>ORCH: tool_use: get_workflow_run_jobs(run_id=123)
    ORCH->>GHA: get_workflow_run_jobs
    GHA-->>ORCH: ジョブ一覧（testが失敗、scan_rubyが失敗）

    ORCH->>Bedrock: Step 3: 推論
    Bedrock-->>ORCH: tool_use: get_job_logs(job_id=456)
    ORCH->>GHA: get_job_logs
    GHA-->>ORCH: ログ（DB接続エラー）

    ORCH->>Bedrock: Step 4: 推論
    Bedrock-->>ORCH: tool_use: get_job_logs(job_id=789)
    ORCH->>GHA: get_job_logs
    GHA-->>ORCH: ログ（Brakeman EOL警告）

    ORCH->>Bedrock: Step 5: レポート生成
    Bedrock-->>ORCH: Markdownレポート

    ORCH-->>IDE: レポート返却
    IDE-->>Dev: "testジョブのDB接続エラーと<br/>Brakemanのセキュリティ警告が原因です"
```

### ユースケース 2: 本番障害の横断調査

```mermaid
graph TB
    subgraph "横断調査フロー"
        Q["開発者: デプロイ後にエラーが出ている"]

        S1["1. ghactions___list_workflow_runs<br/>→ 直近のデプロイ状態確認"]
        S2["2. ghactions___get_job_logs<br/>→ デプロイログ確認"]
        S3["3. cwlogs___query_cloudwatch_insights<br/>→ ECSアプリログでエラー検索"]
        S4["4. rdsquery___list_tables<br/>→ テーブル構成確認"]
        S5["5. rdsquery___query_rds<br/>→ マイグレーション状態確認"]
        S6["6. Markdown レポート出力"]

        Q --> S1
        S1 --> S2
        S2 --> S3
        S3 --> S4
        S4 --> S5
        S5 --> S6
    end

    style S1 fill:#f9e79f,stroke:#f39c12
    style S2 fill:#f9e79f,stroke:#f39c12
    style S3 fill:#aed6f1,stroke:#2980b9
    style S4 fill:#d5f5e3,stroke:#27ae60
    style S5 fill:#d5f5e3,stroke:#27ae60
    style S6 fill:#e8daef,stroke:#8e44ad
```

**全ステップが自動実行**される。ユーザーは最初の1回の指示のみ。

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
        L3A["読み取り専用<br/>全サブツールがGET/SELECT"]
        L3B["ReActステップ数制限<br/>最大20ステップ"]
        L3C["ツール結果サイズ制限<br/>最大10,000文字"]
        L3D["Bedrockトークン制限<br/>最大4,096トークン"]
    end

    subgraph "Layer 4: Bedrock制御"
        L4A["IAMロール最小権限<br/>InvokeModel のみ"]
        L4B["モデル指定<br/>Sonnet 4 固定"]
    end

    subgraph "Layer 5: サブツール制御"
        L5["各MCPサーバーの<br/>既存セキュリティ層<br/>(読取専用, 許可リスト, etc.)"]
    end

    L1 --> L2 --> L3A
    L3A --> L3B --> L3C --> L3D
    L3D --> L4A --> L4B
    L4B --> L5

    style L3B fill:#fadbd8,stroke:#e74c3c
    style L3C fill:#fadbd8,stroke:#e74c3c
    style L4A fill:#fadbd8,stroke:#e74c3c
```

### Bedrock IAM ポリシー

```json
{
  "Statement": [
    {
      "Sid": "BedrockInvokeModel",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.*"
      ]
    }
  ]
}
```

### 安全な設計ポイント

| ポイント | 説明 |
|---|---|
| サブツールは読み取り専用 | CloudWatch=クエリ、RDS=SELECT、GitHub=GET API |
| 結果のtruncate | LLMコンテキスト溢れを防止（10,000文字/ツール結果） |
| ステップ数上限 | 無限ループ防止（最大20ステップ） |
| Bedrock認証 | IAMロールによるサービス間認証 |
| シークレット管理 | Secrets Manager経由でランタイム設定を注入 |

---

## ディレクトリ構成

```
devops_agent/
├── mcp_server.py                          # 既存: CloudWatch MCP Server
├── rds_mcp_server.py                      # 既存: RDS MCP Server
├── gha_mcp_server.py                      # 既存: GitHub Actions MCP Server
├── orchestrator_mcp_server.py             # 新規: Orchestrator MCP Server
├── Dockerfile                             # 既存: CloudWatch用
├── Dockerfile.rds                         # 既存: RDS用
├── Dockerfile.gha                         # 既存: GitHub Actions用
├── Dockerfile.orchestrator                # 新規: Orchestrator用
├── requirements.txt                       # 既存
├── requirements-rds.txt                   # 既存
├── requirements-gha.txt                   # 既存
├── requirements-orchestrator.txt          # 新規
└── terraform/
    ├── # 既存ファイル（変更あり）
    ├── locals.tf                          # 更新: Orchestrator用ローカル変数追加
    ├── variables.tf                       # 更新: Orchestrator用変数追加
    ├── outputs.tf                         # 更新: Orchestrator用出力追加
    ├── # 新規ファイル
    ├── orchestrator_runtime.tf            # 新規: Runtime + ECR + IAM + Secrets
    ├── orchestrator_gateway_target.tf     # 新規: Gateway Target追加
    └── templates/
        ├── orchestrator_runtime.yaml.tftpl         # 新規: Runtime CFn テンプレート
        └── orchestrator_gateway_target.yaml.tftpl  # 新規: Gateway Target CFn テンプレート
```

---

## Terraform リソース追加一覧

```mermaid
graph TB
    subgraph "新規 Terraform リソース"
        subgraph "Secrets"
            SM_CFG["aws_secretsmanager_secret<br/>orchestrator_runtime_config"]
        end

        subgraph "AgentCore Runtime"
            ECR["aws_ecr_repository<br/>orchestrator_runtime"]
            IAM_RT["aws_iam_role + policy<br/>orchestrator_runtime<br/>(Bedrock InvokeModel 権限付き)"]
            CF_RT["aws_cloudformation_stack<br/>orchestrator_runtime"]
        end

        subgraph "AgentCore Gateway Target"
            CF_GT["aws_cloudformation_stack<br/>orchestrator_gateway_target"]
        end
    end

    CF_RT --> ECR
    CF_RT --> SM_CFG
    CF_RT --> IAM_RT
    CF_GT --> CF_RT

    style IAM_RT fill:#fadbd8,stroke:#e74c3c
    style CF_RT fill:#e8daef,stroke:#8e44ad
    style CF_GT fill:#d5f5e3,stroke:#27ae60
```

### 既存MCPサーバーとの比較

| 項目 | CloudWatch MCP | RDS MCP | GitHub Actions MCP | Orchestrator MCP (新規) |
|---|---|---|---|---|
| データソース | CloudWatch Logs | RDS PostgreSQL | GitHub REST API | **既存MCP全ツール + Bedrock** |
| 接続方式 | boto3 (IAM) | Lambda Proxy | httpx + PAT | **httpx (Gateway) + boto3 (Bedrock)** |
| ネットワーク | Public API | Lambda (VPC) | Public API | **Public API** |
| 認証 | IAMロール | IAMロール + DB | GitHub PAT | **IAMロール (Bedrock)** |
| Lambda | 不要 | 必要 | 不要 | **不要** |
| VPC | 不要 | 必要 | 不要 | **不要** |
| Target名 | `cwlogs` | `rdsquery` | `ghactions` | **`orchestrator`** |
| ツール数 | 1 | 3 | 5 | **1** |
| Container | `Dockerfile` | `Dockerfile.rds` | `Dockerfile.gha` | **`Dockerfile.orchestrator`** |
| 追加依存 | boto3 | boto3 | httpx, boto3 | **httpx, boto3** |
| 複雑度 | 低 | 高 | 低 | **中（ReActロジック）** |

---

## 設定値一覧

### 環境変数 (Orchestrator MCP Server)

| 変数名 | 説明 | デフォルト |
|---|---|---|
| `BEDROCK_MODEL_ID` | Bedrock推論プロファイルID | `us.anthropic.claude-sonnet-4-20250514-v1:0` |
| `GATEWAY_MCP_URL` | AgentCore GatewayのMCPエンドポイントURL | (必須) |
| `MAX_REACT_STEPS` | ReActループの最大ステップ数 | `20` |
| `BEDROCK_MAX_TOKENS` | Bedrockレスポンスの最大トークン数 | `4096` |
| `TOOL_RESULT_MAX_CHARS` | ツール結果のtruncate上限文字数 | `10000` |
| `RUNTIME_CONFIG_SECRET_ID` | Runtime設定のSecrets Manager ARN | (必須) |

### Terraform 変数

| 変数名 | 説明 | デフォルト |
|---|---|---|
| `orchestrator_runtime_image_tag` | コンテナイメージタグ | `latest` |
| `orchestrator_bedrock_model_id` | Bedrock推論プロファイルID | `us.anthropic.claude-sonnet-4-20250514-v1:0` |
| `orchestrator_max_react_steps` | ReActループ最大ステップ数 | `10` |
| `orchestrator_bedrock_max_tokens` | Bedrockレスポンス最大トークン数 | `4096` |

---

## 実装ステップ

```mermaid
gantt
    title Orchestrator MCP Server 実装計画
    dateFormat YYYY-MM-DD
    section Phase 1: MCP Server
        orchestrator_mcp_server.py 実装        :done, p1a, 2026-03-20, 1d
        Dockerfile + requirements 作成          :done, p1b, 2026-03-20, 1d
    section Phase 2: Terraform
        orchestrator_runtime.tf + CFn テンプレート :done, p2a, 2026-03-20, 1d
        orchestrator_gateway_target.tf            :done, p2b, 2026-03-20, 1d
        locals / variables / outputs 更新         :done, p2c, 2026-03-20, 1d
    section Phase 3: ローカルテスト
        GHA MCP + Orchestrator 統合テスト         :done, p3a, 2026-03-20, 1d
    section Phase 4: デプロイ
        ECR push + terraform apply               :p4a, 2026-03-21, 1d
    section Phase 5: 統合テスト
        MCP Client → Gateway → 全ツール横断テスト :p5a, after p4a, 1d
```

---

## データフロー詳細

```mermaid
sequenceDiagram
    actor User as 開発者
    participant IDE as Cursor
    participant GW as AgentCore Gateway
    participant Cognito as Cognito
    participant ORCH as Orchestrator Runtime
    participant Bedrock as Bedrock Claude
    participant GHA as GitHub Actions Runtime
    participant CW as CloudWatch Runtime

    User->>IDE: "デプロイ後にエラーが出ている原因を調べて"
    IDE->>GW: POST /mcp (tool: orchestrator___investigate_error)

    Note over GW,Cognito: OAuth2 認証フロー
    GW->>Cognito: POST /oauth2/token (client_credentials)
    Cognito-->>GW: access_token (JWT)
    GW->>ORCH: MCP tool call + Bearer token

    rect rgb(243, 229, 245)
        Note over ORCH: ReAct ループ開始

        ORCH->>Bedrock: Converse(messages, tools)
        Bedrock-->>ORCH: tool_use: ghactions___list_workflow_runs

        ORCH->>GW: POST /mcp (ghactions___list_workflow_runs)
        GW->>GHA: Forward
        GHA-->>GW: Workflow runs JSON
        GW-->>ORCH: SSE response

        ORCH->>Bedrock: Converse(messages + tool_result)
        Bedrock-->>ORCH: tool_use: ghactions___get_job_logs

        ORCH->>GW: POST /mcp (ghactions___get_job_logs)
        GW->>GHA: Forward
        GHA-->>GW: Job logs
        GW-->>ORCH: SSE response

        ORCH->>Bedrock: Converse(messages + tool_result)
        Bedrock-->>ORCH: tool_use: cwlogs___query_cloudwatch_insights

        ORCH->>GW: POST /mcp (cwlogs___query_cloudwatch_insights)
        GW->>CW: Forward
        CW-->>GW: Log query results
        GW-->>ORCH: SSE response

        ORCH->>Bedrock: Converse(messages + tool_result)
        Bedrock-->>ORCH: end_turn → Markdownレポート
    end

    ORCH-->>GW: {"ok": true, "report": "# Investigation Report\n..."}
    GW-->>IDE: MCP tool result
    IDE-->>User: Markdownレポート表示
```

---

## Cursor 連携

### 設定

`.mcp.json` はGateway URLを指しており、オーケストレーターのデプロイ後は自動的に `orchestrator___investigate_error` ツールが利用可能になる。**設定変更は不要**。

```json
{
  "mcpServers": {
    "devops-agent": {
      "url": "https://devops-agent-prod-xxxxx-gateway-xxxxx.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
    }
  }
}
```

### 利用イメージ

```
ユーザー: @devops-agent 直近のデプロイが失敗した原因を調べて

Cursor: orchestrator___investigate_error を呼び出しています...

(内部でBedrock Claudeが GitHub Actions → CloudWatch → RDS を自動調査)

Cursor:
# Investigation Report

## Summary
CIが失敗した主な原因は、データベース接続の問題と...

## Root Cause
1. PostgreSQLサーバーへの接続が拒否...
2. BrakemanがEOLのRails/Rubyバージョンを検出...

## Recommended Actions
1. CI workflow内のPostgreSQLサービス設定を確認...
2. Railsバージョンのアップグレード...
```

---

## 実装済みテスト結果（2026-03-20）

ローカル環境でオーケストレーター → GitHub Actions MCPサーバーの統合テストを実施。

### テスト構成

```
Orchestrator (port 8002) → GHA MCP Server (port 8001) → GitHub API
      ↕
Bedrock Claude Sonnet 4 (us-east-1)
```

### テスト入力

```
"直近のCIが失敗した原因を調査して"
```

### 結果

| 項目 | 値 |
|---|---|
| 実行時間 | 91.6秒 |
| ReActステップ数 | 約15ステップ |
| 呼び出されたツール | list_workflow_runs, get_workflow_run_jobs, get_job_logs (複数回) |
| 特定された原因 | DB接続エラー + Brakeman EOL警告 |
| レポート品質 | Summary, Root Cause, Evidence, Recommended Actions 全セクション生成 |

### 生成されたレポート（抜粋）

```markdown
# Investigation Report

## Summary
CIが失敗した主な原因は、データベース接続の問題とセキュリティ検査（Brakeman）での
警告です。

## Root Cause
1. **データベース接続エラー**: PostgreSQLサーバーへの接続が拒否され、
   `rails db:prepare`コマンドが失敗
2. **セキュリティ警告**: Brakemanがend-of-life（EOL）のRailsとRubyバージョンを検出

## Evidence
### テストジョブの失敗
ActiveRecord::ConnectionNotEstablished: connection to server at "::1",
port 5432 failed: Connection refused

### セキュリティスキャンの警告
Support for Rails 7.1.6 ended on 2025-10-01
Support for Ruby 3.2.9 ends on 2026-03-31
```
