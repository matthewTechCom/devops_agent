import json
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

load_dotenv()


def bootstrap_aws_region() -> str:
    return os.getenv("AWS_REGION", "us-east-1")


def parse_remote_runtime_config(raw_payload: str, *, source: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{source} must contain a valid JSON object.") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"{source} must contain a JSON object at the top level.")

    return payload


def apply_remote_runtime_config(payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        if value is None:
            continue

        if isinstance(value, bool):
            normalized = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            normalized = str(value)
        elif isinstance(value, list):
            normalized = ",".join(str(item) for item in value)
        else:
            raise RuntimeError(
                f"Unsupported value type for runtime config key '{key}': {type(value).__name__}."
            )

        os.environ[key] = normalized


def load_runtime_config_from_secrets_manager(secret_id: str) -> None:
    client = boto3.client("secretsmanager", region_name=bootstrap_aws_region())
    response = client.get_secret_value(SecretId=secret_id)
    secret_string = response.get("SecretString")
    if not secret_string:
        raise RuntimeError("Secrets Manager runtime config must be stored as SecretString.")

    apply_remote_runtime_config(
        parse_remote_runtime_config(secret_string, source=f"Secrets Manager secret '{secret_id}'")
    )


def load_runtime_config_from_ssm(parameter_name: str) -> None:
    client = boto3.client("ssm", region_name=bootstrap_aws_region())
    response = client.get_parameter(Name=parameter_name, WithDecryption=True)
    parameter_value = response["Parameter"]["Value"]

    apply_remote_runtime_config(
        parse_remote_runtime_config(parameter_value, source=f"SSM parameter '{parameter_name}'")
    )


def bootstrap_runtime_config() -> str:
    secret_id = os.getenv("RUNTIME_CONFIG_SECRET_ID", "").strip()
    parameter_name = os.getenv("RUNTIME_CONFIG_SSM_PARAMETER_NAME", "").strip()

    if secret_id and parameter_name:
        raise RuntimeError(
            "Configure only one of RUNTIME_CONFIG_SECRET_ID or RUNTIME_CONFIG_SSM_PARAMETER_NAME."
        )

    if secret_id:
        load_runtime_config_from_secrets_manager(secret_id)
        return "secretsmanager"

    if parameter_name:
        load_runtime_config_from_ssm(parameter_name)
        return "ssm"

    return "environment"


RUNTIME_CONFIG_SOURCE = bootstrap_runtime_config()
AWS_REGION = bootstrap_aws_region()
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
GATEWAY_MCP_URL = os.getenv("GATEWAY_MCP_URL", "").strip()
MAX_REACT_STEPS = int(os.getenv("MAX_REACT_STEPS", "10"))
BEDROCK_MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "4096"))
TOOL_RESULT_MAX_CHARS = int(os.getenv("TOOL_RESULT_MAX_CHARS", "10000"))

mcp = FastMCP(
    "orchestrator-mcp",
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
    stateless_http=True,
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def error_response(message: str, *, error_type: str = "Error", **extra: Any) -> dict[str, Any]:
    payload = {
        "ok": False,
        "error_type": error_type,
        "message": message,
        "region": AWS_REGION,
    }
    payload.update(extra)
    return payload


async def healthz(_request) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "service": "orchestrator-mcp",
            "region": AWS_REGION,
            "bedrock_model_id": BEDROCK_MODEL_ID,
            "gateway_mcp_url": GATEWAY_MCP_URL or None,
            "max_react_steps": MAX_REACT_STEPS,
            "runtime_config_source": RUNTIME_CONFIG_SOURCE,
        }
    )


def build_app() -> Starlette:
    base_app = mcp.streamable_http_app()
    mcp_route = next(route for route in base_app.routes if getattr(route, "path", None) == "/mcp")
    app = Starlette(
        debug=base_app.debug,
        routes=[
            Route("/mcp", endpoint=mcp_route.endpoint),
            Route("/mcp/", endpoint=mcp_route.endpoint),
            Route("/healthz", endpoint=healthz, methods=["GET"]),
        ],
        middleware=base_app.user_middleware,
        lifespan=base_app.router.lifespan_context,
    )
    app.router.redirect_slashes = False
    return app


# ---------------------------------------------------------------------------
# Bedrock Converse API helpers
# ---------------------------------------------------------------------------

def bedrock_client():
    # On AWS (ECS/AgentCore), credentials come from the instance role, not a profile.
    # Locally, .env may set AWS_PROFILE which can conflict if ~/.aws/config is absent.
    # Create a session without profile_name to use env var credentials directly.
    profile = os.environ.pop("AWS_PROFILE", None)
    try:
        session = boto3.Session(region_name=AWS_REGION)
        return session.client("bedrock-runtime")
    finally:
        if profile is not None:
            os.environ["AWS_PROFILE"] = profile


SYSTEM_PROMPT = """You are a DevOps investigation agent for a production application.
You have access to the following tool categories:

**CloudWatch Logs Insights** (via cwlogs___ prefix):
- cwlogs___query_cloudwatch_insights: Query CloudWatch Logs Insights. Args: log_group_name (str), minutes (int), query (str).

**RDS Database** (via rdsquery___ prefix):
- rdsquery___query_rds: Execute a read-only SQL query. Args: query (str), max_rows (int, optional).
- rdsquery___list_tables: List all database tables. No args.
- rdsquery___describe_table: Describe a table's schema. Args: table_name (str).

**GitHub Actions** (via ghactions___ prefix):
- ghactions___list_workflows: List all workflow definitions. No args.
- ghactions___list_workflow_runs: List recent workflow runs. Args: status (str, optional), branch (str, optional), limit (int, optional).
- ghactions___get_workflow_run: Get details of a specific run. Args: run_id (int).
- ghactions___get_workflow_run_jobs: Get jobs and step statuses for a run. Args: run_id (int).
- ghactions___get_job_logs: Get logs for a specific job. Args: job_id (int), tail_lines (int, optional).

## Instructions

1. Analyze the user's question to understand the issue.
2. Call tools step by step to gather evidence. Start broadly, then drill down.
3. When you have enough evidence, produce a comprehensive Markdown investigation report.

## Report Format

Your final response MUST be a Markdown report with the following structure:

# Investigation Report

## Summary
(One-paragraph executive summary of the finding)

## Investigation Steps
(Numbered list of what you checked and what you found)

## Root Cause
(Clear explanation of the root cause)

## Evidence
(Key log entries, query results, or job outputs that support your conclusion)

## Recommended Actions
(Concrete next steps to resolve the issue)

---
*Generated at: {timestamp} by DevOps Investigation Agent*
"""

BEDROCK_TOOL_DEFINITIONS = [
    {
        "toolSpec": {
            "name": "cwlogs___query_cloudwatch_insights",
            "description": "Query CloudWatch Logs Insights against approved log groups. Provide a log group name, time range in minutes, and a CloudWatch Insights query string.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "log_group_name": {"type": "string", "description": "CloudWatch log group name. Use 'default' for the pre-configured log group."},
                        "minutes": {"type": "integer", "description": "Time range in minutes to query from now."},
                        "query": {"type": "string", "description": "CloudWatch Insights query string."},
                    },
                    "required": ["log_group_name", "minutes", "query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "rdsquery___query_rds",
            "description": "Execute a read-only SQL query against the application's RDS PostgreSQL database. Only SELECT, WITH (CTE), and EXPLAIN statements are allowed.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "SQL query to execute (SELECT only)."},
                        "max_rows": {"type": "integer", "description": "Maximum rows to return (default: 100, max: 1000)."},
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "rdsquery___list_tables",
            "description": "List all tables in the application's RDS PostgreSQL database with row counts.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {},
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "rdsquery___describe_table",
            "description": "Describe a table's schema (columns, types, indexes) in the application's RDS PostgreSQL database.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string", "description": "Name of the table to describe."},
                    },
                    "required": ["table_name"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "ghactions___list_workflows",
            "description": "List all workflow definitions in the repository.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {},
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "ghactions___list_workflow_runs",
            "description": "List recent GitHub Actions workflow runs with optional filters.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "description": "Filter by status: completed, in_progress, queued, success, failure."},
                        "branch": {"type": "string", "description": "Filter by branch name."},
                        "limit": {"type": "integer", "description": "Number of runs to return (default: 10, max: 30)."},
                    },
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "ghactions___get_workflow_run",
            "description": "Get details of a specific GitHub Actions workflow run.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "integer", "description": "The workflow run ID."},
                    },
                    "required": ["run_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "ghactions___get_workflow_run_jobs",
            "description": "Get all jobs and their step statuses for a GitHub Actions workflow run.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "integer", "description": "The workflow run ID."},
                    },
                    "required": ["run_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "ghactions___get_job_logs",
            "description": "Get logs for a specific GitHub Actions job. Returns the last N lines of log output.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "integer", "description": "The job ID (from get_workflow_run_jobs)."},
                        "tail_lines": {"type": "integer", "description": "Number of lines from end of log to return (default: 100, max: 500)."},
                    },
                    "required": ["job_id"],
                }
            },
        }
    },
]


# ---------------------------------------------------------------------------
# MCP Gateway client – call sub-tools via the AgentCore Gateway
# ---------------------------------------------------------------------------

def _sigv4_headers(url: str, body: bytes) -> dict[str, str]:
    """Generate SigV4 signed headers for a POST request to AgentCore Gateway."""
    session = boto3.Session(region_name=AWS_REGION)
    credentials = session.get_credentials().get_frozen_credentials()
    parsed = urlparse(url)
    request = AWSRequest(
        method="POST",
        url=url,
        data=body,
        headers={"Content-Type": "application/json", "Host": parsed.hostname},
    )
    SigV4Auth(credentials, "bedrock-agentcore", AWS_REGION).add_auth(request)
    return dict(request.headers)


def call_mcp_tool_via_gateway(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool on the AgentCore Gateway using JSON-RPC over HTTP with SigV4 auth."""
    if not GATEWAY_MCP_URL:
        return {"ok": False, "error": "GATEWAY_MCP_URL is not configured."}

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    body = json.dumps(payload).encode("utf-8")

    try:
        signed_headers = _sigv4_headers(GATEWAY_MCP_URL, body)
        signed_headers["Accept"] = "application/json, text/event-stream"

        with httpx.Client(timeout=90.0) as client:
            response = client.post(
                GATEWAY_MCP_URL,
                content=body,
                headers=signed_headers,
            )
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                return _parse_sse_response(response.text)
            return response.json()
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"Gateway HTTP error {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        return {"ok": False, "error": f"Gateway call failed: {exc}"}


def _parse_sse_response(text: str) -> dict[str, Any]:
    """Parse Server-Sent Events response from MCP gateway."""
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                data = json.loads(line[6:])
                if "result" in data:
                    result = data["result"]
                    if "structuredContent" in result:
                        return result["structuredContent"]
                    if "content" in result:
                        for block in result["content"]:
                            if block.get("type") == "text":
                                try:
                                    return json.loads(block["text"])
                                except json.JSONDecodeError:
                                    return {"ok": True, "text": block["text"]}
                return data
            except json.JSONDecodeError:
                continue
    return {"ok": False, "error": "No valid data in SSE response"}


def truncate_result(result: Any) -> str:
    """Truncate tool result to avoid exceeding Bedrock context limits."""
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > TOOL_RESULT_MAX_CHARS:
        return text[:TOOL_RESULT_MAX_CHARS] + "\n... [truncated]"
    return text


# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------

def react_loop(user_query: str) -> str:
    """Run a ReAct reasoning loop using Bedrock Claude to investigate an issue."""
    system_prompt = SYSTEM_PROMPT.replace("{timestamp}", now_utc().isoformat())

    messages = [{"role": "user", "content": [{"text": user_query}]}]

    client = bedrock_client()

    for step in range(MAX_REACT_STEPS):
        try:
            response = client.converse(
                modelId=BEDROCK_MODEL_ID,
                system=[{"text": system_prompt}],
                messages=messages,
                toolConfig={"tools": BEDROCK_TOOL_DEFINITIONS},
                inferenceConfig={"maxTokens": BEDROCK_MAX_TOKENS},
            )
        except (ClientError, BotoCoreError) as exc:
            return f"# Investigation Error\n\nFailed to call Bedrock model: {exc}"

        output_message = response["output"]["message"]
        messages.append(output_message)
        stop_reason = response["stopReason"]

        if stop_reason == "end_turn":
            return _extract_text(output_message)

        if stop_reason == "tool_use":
            tool_results = []
            for content_block in output_message["content"]:
                if "toolUse" in content_block:
                    tool_use = content_block["toolUse"]
                    tool_name = tool_use["name"]
                    tool_input = tool_use["input"]
                    tool_use_id = tool_use["toolUseId"]

                    result = call_mcp_tool_via_gateway(tool_name, tool_input)
                    truncated = truncate_result(result)

                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": truncated}],
                        }
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            return _extract_text(output_message)

    return "# Investigation Incomplete\n\nReached the maximum number of investigation steps. Partial findings may be available in the conversation history."


def _extract_text(message: dict[str, Any]) -> str:
    """Extract text content from a Bedrock Converse response message."""
    parts = []
    for block in message.get("content", []):
        if "text" in block:
            parts.append(block["text"])
    return "\n".join(parts) if parts else "(No text content in response)"


# ---------------------------------------------------------------------------
# MCP tool: investigate_error
# ---------------------------------------------------------------------------

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
    query = (query or "").strip()
    if not query:
        return error_response("query is required.", error_type="ValidationError")

    if not GATEWAY_MCP_URL:
        return error_response(
            "GATEWAY_MCP_URL must be configured to call sub-tools.",
            error_type="ConfigurationError",
        )

    start_time = time.monotonic()

    try:
        report = react_loop(query)
        elapsed_seconds = round(time.monotonic() - start_time, 1)

        return {
            "ok": True,
            "report": report,
            "model": BEDROCK_MODEL_ID,
            "elapsed_seconds": elapsed_seconds,
            "queried_at": now_utc().isoformat(),
        }
    except Exception as exc:
        return error_response(
            f"Investigation failed: {exc}",
            error_type="OrchestrationError",
        )


if __name__ == "__main__":
    uvicorn.run(build_app(), host=HOST, port=PORT, log_level="info")
