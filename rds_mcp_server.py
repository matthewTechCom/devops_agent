import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

load_dotenv()


def bootstrap_aws_region() -> str:
    return os.getenv("AWS_REGION", "ap-northeast-1")


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
RDS_LAMBDA_FUNCTION_NAME = os.getenv("RDS_LAMBDA_FUNCTION_NAME", "").strip()
LAMBDA_TIMEOUT_SECONDS = int(os.getenv("LAMBDA_TIMEOUT_SECONDS", "60"))
QUERY_MAX_ROWS = int(os.getenv("QUERY_MAX_ROWS", "1000"))

mcp = FastMCP(
    "rds-query-mcp",
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
    stateless_http=True,
)


def lambda_client():
    return boto3.client("lambda", region_name=AWS_REGION)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def invoke_lambda(payload: dict[str, Any]) -> dict[str, Any]:
    if not RDS_LAMBDA_FUNCTION_NAME:
        return {
            "ok": False,
            "error": "RDS_LAMBDA_FUNCTION_NAME is not configured.",
        }

    client = lambda_client()

    try:
        response = client.invoke(
            FunctionName=RDS_LAMBDA_FUNCTION_NAME,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8"),
        )
    except (ClientError, BotoCoreError) as exc:
        return {
            "ok": False,
            "error": f"Failed to invoke Lambda: {exc}",
        }

    response_payload = response["Payload"].read().decode("utf-8")

    if response.get("FunctionError"):
        return {
            "ok": False,
            "error": f"Lambda execution error: {response_payload}",
        }

    try:
        return json.loads(response_payload)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": f"Invalid JSON response from Lambda: {response_payload[:500]}",
        }


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
            "service": "rds-query-mcp",
            "region": AWS_REGION,
            "lambda_function_name": RDS_LAMBDA_FUNCTION_NAME or None,
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


@mcp.tool()
def query_rds(query: str, max_rows: int = 100) -> dict[str, Any]:
    """Execute a read-only SQL query against the todo application's RDS PostgreSQL database.

    Only SELECT, WITH (CTE), and EXPLAIN statements are allowed.
    Results are limited by max_rows (maximum 1000).

    Args:
        query: SQL query to execute (SELECT only).
        max_rows: Maximum number of rows to return (default: 100, max: 1000).
    """
    query = (query or "").strip()
    if not query:
        return error_response("query is required.", error_type="ValidationError")

    max_rows = max(1, min(max_rows, QUERY_MAX_ROWS))

    start_time = time.monotonic()
    result = invoke_lambda({
        "action": "query",
        "sql": query,
        "max_rows": max_rows,
    })
    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    if result.get("ok"):
        result["query"] = query
        result["execution_time_ms"] = elapsed_ms
        result["queried_at"] = now_utc().isoformat()

    return result


@mcp.tool()
def list_tables() -> dict[str, Any]:
    """List all tables in the todo application's RDS PostgreSQL database with row counts."""
    start_time = time.monotonic()
    result = invoke_lambda({"action": "list_tables"})
    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    if result.get("ok"):
        result["execution_time_ms"] = elapsed_ms
        result["queried_at"] = now_utc().isoformat()

    return result


@mcp.tool()
def describe_table(table_name: str) -> dict[str, Any]:
    """Describe a table's schema (columns, types, indexes) in the todo application's RDS PostgreSQL database.

    Args:
        table_name: Name of the table to describe.
    """
    table_name = (table_name or "").strip()
    if not table_name:
        return error_response("table_name is required.", error_type="ValidationError")

    start_time = time.monotonic()
    result = invoke_lambda({
        "action": "describe_table",
        "table_name": table_name,
    })
    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    if result.get("ok"):
        result["execution_time_ms"] = elapsed_ms
        result["queried_at"] = now_utc().isoformat()

    return result


if __name__ == "__main__":
    uvicorn.run(build_app(), host=HOST, port=PORT, log_level="info")
