import os
import time
from datetime import datetime, timedelta, timezone
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

AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-1")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
QUERY_TIMEOUT_SECONDS = int(os.getenv("QUERY_TIMEOUT_SECONDS", "45"))
QUERY_POLL_SECONDS = float(os.getenv("QUERY_POLL_SECONDS", "1.5"))
TARGET_APP_NAME = os.getenv("TARGET_APP_NAME", "").strip()
TARGET_APP_ENV = os.getenv("TARGET_APP_ENV", "").strip()
TARGET_APP_COMPONENT = os.getenv("TARGET_APP_COMPONENT", "backend").strip() or "backend"
DEFAULT_LOG_GROUP_NAME = os.getenv("DEFAULT_LOG_GROUP_NAME", "").strip()
ALLOWED_LOG_GROUP_NAMES = [name.strip() for name in os.getenv("ALLOWED_LOG_GROUP_NAMES", "").split(",") if name.strip()]

mcp = FastMCP(
    "cloudwatch-insights-mcp",
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
    stateless_http=True,
)


def logs_client():
    return boto3.client("logs", region_name=AWS_REGION)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def inferred_default_log_group_name() -> str | None:
    if DEFAULT_LOG_GROUP_NAME:
        return DEFAULT_LOG_GROUP_NAME

    if TARGET_APP_NAME and TARGET_APP_ENV:
        return f"/ecs/{TARGET_APP_NAME}-{TARGET_APP_ENV}-{TARGET_APP_COMPONENT}"

    return None


def effective_allowed_log_group_names() -> list[str]:
    if ALLOWED_LOG_GROUP_NAMES:
        return ALLOWED_LOG_GROUP_NAMES

    default_log_group_name = inferred_default_log_group_name()
    if default_log_group_name:
        return [default_log_group_name]

    return []


def resolve_log_group_name(log_group_name: str) -> str | None:
    candidate = (log_group_name or "").strip()
    default_log_group_name = inferred_default_log_group_name()

    if candidate.lower() in {"", "default", "@default"}:
        return default_log_group_name

    return candidate or default_log_group_name


def normalize_results(results: list[list[dict[str, str]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for row in results:
        normalized: dict[str, Any] = {}
        for cell in row:
            field = cell.get("field", "")
            value = cell.get("value")

            if not field:
                continue

            if field in normalized:
                if isinstance(normalized[field], list):
                    normalized[field].append(value)
                else:
                    normalized[field] = [normalized[field], value]
            else:
                normalized[field] = value

        rows.append(normalized)

    return rows


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
            "region": AWS_REGION,
            "default_log_group_name": inferred_default_log_group_name(),
            "allowed_log_group_names": effective_allowed_log_group_names(),
            "target_app": {
                "name": TARGET_APP_NAME or None,
                "environment": TARGET_APP_ENV or None,
                "component": TARGET_APP_COMPONENT,
            },
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
def query_cloudwatch_insights(log_group_name: str, minutes: int, query: str) -> dict[str, Any]:
    log_group_name = resolve_log_group_name(log_group_name)
    query = (query or "").strip()
    allowed_log_groups = effective_allowed_log_group_names()

    if not log_group_name:
        return error_response(
            "log_group_name is required. Set it explicitly or configure DEFAULT_LOG_GROUP_NAME / TARGET_APP_* in .env.",
            error_type="ValidationError",
        )

    if not query:
        return error_response("query is required.", error_type="ValidationError")

    if minutes <= 0:
        return error_response("minutes must be greater than 0.", error_type="ValidationError")

    if allowed_log_groups and log_group_name not in allowed_log_groups:
        return error_response(
            "The requested log_group_name is not allowed by this MCP server configuration.",
            error_type="AccessDenied",
            log_group_name=log_group_name,
            allowed_log_group_names=allowed_log_groups,
        )

    end_time = now_utc()
    start_time = end_time - timedelta(minutes=minutes)
    client = logs_client()

    try:
        start_response = client.start_query(
            logGroupName=log_group_name,
            startTime=int(start_time.timestamp()),
            endTime=int(end_time.timestamp()),
            queryString=query,
        )
        query_id = start_response["queryId"]
    except (ClientError, BotoCoreError) as exc:
        return error_response(
            "Failed to start the CloudWatch Logs Insights query.",
            error_type=type(exc).__name__,
            details=str(exc),
            log_group_name=log_group_name,
            minutes=minutes,
            query=query,
        )

    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        try:
            result_response = client.get_query_results(queryId=query_id)
        except (ClientError, BotoCoreError) as exc:
            return error_response(
                "Failed to fetch CloudWatch Logs Insights query results.",
                error_type=type(exc).__name__,
                details=str(exc),
                query_id=query_id,
                log_group_name=log_group_name,
                minutes=minutes,
                query=query,
            )

        status = result_response.get("status", "Unknown")
        if status == "Complete":
            return {
                "ok": True,
                "region": AWS_REGION,
                "log_group_name": log_group_name,
                "minutes": minutes,
                "query": query,
                "query_id": query_id,
                "status": status,
                "results": normalize_results(result_response.get("results", [])),
                "statistics": result_response.get("statistics", {}),
                "queried_at": end_time.isoformat(),
                "target_app": {
                    "name": TARGET_APP_NAME or None,
                    "environment": TARGET_APP_ENV or None,
                    "component": TARGET_APP_COMPONENT,
                    "default_log_group_name": inferred_default_log_group_name(),
                },
            }

        if status in {"Failed", "Cancelled", "Timeout", "Unknown"}:
            return error_response(
                "CloudWatch Logs Insights query did not complete successfully.",
                error_type="QueryExecutionError",
                query_id=query_id,
                status=status,
                log_group_name=log_group_name,
                minutes=minutes,
                query=query,
                statistics=result_response.get("statistics", {}),
            )

        time.sleep(QUERY_POLL_SECONDS)

    return error_response(
        "Timed out while waiting for CloudWatch Logs Insights query results.",
        error_type="QueryTimeoutError",
        query_id=query_id,
        timeout_seconds=QUERY_TIMEOUT_SECONDS,
        log_group_name=log_group_name,
        minutes=minutes,
        query=query,
    )


if __name__ == "__main__":
    uvicorn.run(build_app(), host=HOST, port=PORT, log_level="info")
