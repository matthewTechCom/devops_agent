import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import boto3
import httpx
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
GITHUB_PAT_SECRET_ID = os.getenv("GITHUB_PAT_SECRET_ID", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()
ALLOWED_REPOSITORIES = [
    r.strip()
    for r in os.getenv("ALLOWED_REPOSITORIES", "").split(",")
    if r.strip()
]
LOG_TAIL_MAX_LINES = int(os.getenv("LOG_TAIL_MAX_LINES", "500"))

GITHUB_API_BASE = "https://api.github.com"

_github_pat_cache: str | None = None


def get_github_pat() -> str:
    global _github_pat_cache
    if _github_pat_cache:
        return _github_pat_cache

    pat = os.getenv("GITHUB_PAT", "").strip()
    if pat:
        _github_pat_cache = pat
        return pat

    if not GITHUB_PAT_SECRET_ID:
        raise RuntimeError("GITHUB_PAT or GITHUB_PAT_SECRET_ID must be configured.")

    client = boto3.client("secretsmanager", region_name=AWS_REGION)
    response = client.get_secret_value(SecretId=GITHUB_PAT_SECRET_ID)
    pat = response["SecretString"].strip()
    _github_pat_cache = pat
    return pat


def effective_repository() -> str:
    if GITHUB_REPOSITORY:
        return GITHUB_REPOSITORY

    if ALLOWED_REPOSITORIES:
        return ALLOWED_REPOSITORIES[0]

    raise RuntimeError("GITHUB_REPOSITORY must be configured.")


def effective_allowed_repositories() -> list[str]:
    if ALLOWED_REPOSITORIES:
        return ALLOWED_REPOSITORIES
    repo = effective_repository()
    return [repo] if repo else []


def github_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | str:
    pat = get_github_pat()
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.get(f"{GITHUB_API_BASE}{path}", headers=headers, params=params)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return response.text


mcp = FastMCP(
    "github-actions-mcp",
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
            "service": "github-actions-mcp",
            "region": AWS_REGION,
            "repository": GITHUB_REPOSITORY or None,
            "allowed_repositories": effective_allowed_repositories(),
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
def list_workflows() -> dict[str, Any]:
    """List all workflow definitions in the repository."""
    repo = effective_repository()
    try:
        data = github_get(f"/repos/{repo}/actions/workflows")
        workflows = [
            {
                "id": w["id"],
                "name": w["name"],
                "path": w["path"],
                "state": w["state"],
            }
            for w in data.get("workflows", [])
        ]
        return {
            "ok": True,
            "repository": repo,
            "workflows": workflows,
            "queried_at": now_utc().isoformat(),
        }
    except httpx.HTTPStatusError as exc:
        return error_response(
            f"GitHub API error: {exc.response.status_code}",
            error_type="GitHubAPIError",
            details=exc.response.text[:500],
        )
    except Exception as exc:
        return error_response(str(exc))


@mcp.tool()
def list_workflow_runs(
    status: str = "",
    branch: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """List recent workflow runs with optional filters.

    Args:
        status: Filter by status ("completed", "in_progress", "queued", "success", "failure").
        branch: Filter by branch name.
        limit: Number of runs to return (default: 10, max: 30).
    """
    repo = effective_repository()
    limit = max(1, min(limit, 30))

    params: dict[str, Any] = {"per_page": limit}
    if status:
        params["status"] = status
    if branch:
        params["branch"] = branch

    try:
        data = github_get(f"/repos/{repo}/actions/runs", params=params)
        runs = []
        for r in data.get("workflow_runs", [])[:limit]:
            created = r.get("created_at", "")
            updated = r.get("updated_at", "")
            duration = None
            if created and updated:
                try:
                    t0 = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    t1 = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    duration = int((t1 - t0).total_seconds())
                except (ValueError, TypeError):
                    pass

            runs.append({
                "id": r["id"],
                "name": r.get("name", ""),
                "status": r.get("status", ""),
                "conclusion": r.get("conclusion"),
                "branch": r.get("head_branch", ""),
                "commit_sha": r.get("head_sha", "")[:7],
                "commit_message": (r.get("head_commit") or {}).get("message", "")[:120],
                "actor": (r.get("actor") or {}).get("login", ""),
                "created_at": created,
                "updated_at": updated,
                "run_duration_seconds": duration,
                "url": r.get("html_url", ""),
            })

        return {
            "ok": True,
            "repository": repo,
            "total_count": data.get("total_count", 0),
            "runs": runs,
            "queried_at": now_utc().isoformat(),
        }
    except httpx.HTTPStatusError as exc:
        return error_response(
            f"GitHub API error: {exc.response.status_code}",
            error_type="GitHubAPIError",
            details=exc.response.text[:500],
        )
    except Exception as exc:
        return error_response(str(exc))


@mcp.tool()
def get_workflow_run(run_id: int) -> dict[str, Any]:
    """Get details of a specific workflow run.

    Args:
        run_id: The workflow run ID.
    """
    repo = effective_repository()
    try:
        r = github_get(f"/repos/{repo}/actions/runs/{run_id}")
        return {
            "ok": True,
            "run": {
                "id": r["id"],
                "name": r.get("name", ""),
                "status": r.get("status", ""),
                "conclusion": r.get("conclusion"),
                "branch": r.get("head_branch", ""),
                "event": r.get("event", ""),
                "commit_sha": r.get("head_sha", ""),
                "commit_message": (r.get("head_commit") or {}).get("message", ""),
                "actor": (r.get("actor") or {}).get("login", ""),
                "triggering_actor": (r.get("triggering_actor") or {}).get("login", ""),
                "created_at": r.get("created_at", ""),
                "updated_at": r.get("updated_at", ""),
                "run_attempt": r.get("run_attempt", 1),
                "url": r.get("html_url", ""),
            },
            "queried_at": now_utc().isoformat(),
        }
    except httpx.HTTPStatusError as exc:
        return error_response(
            f"GitHub API error: {exc.response.status_code}",
            error_type="GitHubAPIError",
            details=exc.response.text[:500],
        )
    except Exception as exc:
        return error_response(str(exc))


@mcp.tool()
def get_workflow_run_jobs(run_id: int) -> dict[str, Any]:
    """Get all jobs and their step statuses for a workflow run.

    Args:
        run_id: The workflow run ID.
    """
    repo = effective_repository()
    try:
        data = github_get(f"/repos/{repo}/actions/runs/{run_id}/jobs")
        jobs = []
        for j in data.get("jobs", []):
            steps = [
                {
                    "number": s.get("number"),
                    "name": s.get("name", ""),
                    "status": s.get("status", ""),
                    "conclusion": s.get("conclusion"),
                }
                for s in j.get("steps", [])
            ]
            jobs.append({
                "id": j["id"],
                "name": j.get("name", ""),
                "status": j.get("status", ""),
                "conclusion": j.get("conclusion"),
                "started_at": j.get("started_at", ""),
                "completed_at": j.get("completed_at", ""),
                "steps": steps,
            })

        return {
            "ok": True,
            "run_id": run_id,
            "jobs": jobs,
            "queried_at": now_utc().isoformat(),
        }
    except httpx.HTTPStatusError as exc:
        return error_response(
            f"GitHub API error: {exc.response.status_code}",
            error_type="GitHubAPIError",
            details=exc.response.text[:500],
        )
    except Exception as exc:
        return error_response(str(exc))


@mcp.tool()
def get_job_logs(job_id: int, tail_lines: int = 100) -> dict[str, Any]:
    """Get logs for a specific job. Returns the last N lines of the log output.

    Args:
        job_id: The job ID (from get_workflow_run_jobs).
        tail_lines: Number of lines from the end of the log to return (default: 100, max: 500).
    """
    repo = effective_repository()
    tail_lines = max(1, min(tail_lines, LOG_TAIL_MAX_LINES))

    try:
        pat = get_github_pat()
        headers = {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(
                f"{GITHUB_API_BASE}/repos/{repo}/actions/jobs/{job_id}/logs",
                headers=headers,
            )
            response.raise_for_status()
            log_text = response.text

        lines = log_text.splitlines()
        total_lines = len(lines)
        truncated = total_lines > tail_lines
        if truncated:
            lines = lines[-tail_lines:]

        return {
            "ok": True,
            "job_id": job_id,
            "total_lines": total_lines,
            "returned_lines": len(lines),
            "truncated": truncated,
            "logs": "\n".join(lines),
            "queried_at": now_utc().isoformat(),
        }
    except httpx.HTTPStatusError as exc:
        return error_response(
            f"GitHub API error: {exc.response.status_code}",
            error_type="GitHubAPIError",
            details=exc.response.text[:500],
        )
    except Exception as exc:
        return error_response(str(exc))


if __name__ == "__main__":
    uvicorn.run(build_app(), host=HOST, port=PORT, log_level="info")
