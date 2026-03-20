"""
Lambda Proxy for RDS PostgreSQL queries.

Runs inside the todo_sample VPC to access the private RDS instance.
Accepts query requests from the RDS MCP Server (via lambda.invoke)
and returns results as JSON.
"""

import json
import os
import re
from typing import Any

import boto3
import psycopg2
import psycopg2.extras

STATEMENT_TIMEOUT_MS = int(os.environ.get("STATEMENT_TIMEOUT_MS", "30000"))
MAX_ROWS = int(os.environ.get("MAX_ROWS", "1000"))
DB_SECRET_ARN = os.environ.get("DB_SECRET_ARN", "")

ALLOWED_STATEMENT_RE = re.compile(
    r"^\s*(SELECT|WITH|EXPLAIN)\b",
    re.IGNORECASE,
)

DENIED_KEYWORD_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|COPY|EXECUTE)\b",
    re.IGNORECASE,
)


def _get_database_url() -> str:
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=DB_SECRET_ARN)
    return response["SecretString"]


def _validate_sql(sql: str) -> str | None:
    if not sql or not sql.strip():
        return "SQL query is required."

    if not ALLOWED_STATEMENT_RE.match(sql):
        return "Only SELECT, WITH (CTE), and EXPLAIN statements are allowed."

    if DENIED_KEYWORD_RE.search(sql):
        return "Query contains a disallowed keyword (INSERT, UPDATE, DELETE, DROP, etc.)."

    return None


def _execute_query(database_url: str, sql: str, max_rows: int) -> dict[str, Any]:
    conn = psycopg2.connect(database_url, connect_timeout=10)
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT_MS}'")
            cur.execute(sql)

            if cur.description is None:
                return {
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "truncated": False,
                }

            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            if truncated:
                rows = rows[:max_rows]

            serializable_rows = []
            for row in rows:
                serializable_rows.append(
                    {k: _serialize_value(v) for k, v in row.items()}
                )

            return {
                "columns": columns,
                "rows": serializable_rows,
                "row_count": len(serializable_rows),
                "truncated": truncated,
            }
    finally:
        conn.close()


def _serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _list_tables(database_url: str) -> dict[str, Any]:
    sql = """
        SELECT
            t.table_name,
            COALESCE(s.n_live_tup, 0) AS row_count
        FROM information_schema.tables t
        LEFT JOIN pg_stat_user_tables s
            ON s.relname = t.table_name AND s.schemaname = t.table_schema
        WHERE t.table_schema = 'public'
          AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name
    """
    result = _execute_query(database_url, sql, MAX_ROWS)
    return {"tables": result["rows"]}


def _describe_table(database_url: str, table_name: str) -> dict[str, Any]:
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table_name):
        return {"error": "Invalid table name."}

    columns_sql = """
        SELECT
            column_name AS name,
            data_type AS type,
            is_nullable = 'YES' AS nullable,
            column_default AS "default"
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """

    indexes_sql = """
        SELECT
            i.relname AS name,
            array_agg(a.attname ORDER BY k.n) AS columns,
            ix.indisunique AS "unique"
        FROM pg_index ix
        JOIN pg_class t ON t.oid = ix.indrelid
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        CROSS JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, n)
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
        WHERE n.nspname = 'public' AND t.relname = %s
        GROUP BY i.relname, ix.indisunique
        ORDER BY i.relname
    """

    conn = psycopg2.connect(database_url, connect_timeout=10)
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT_MS}'")

            cur.execute(columns_sql, (table_name,))
            columns = [
                {k: _serialize_value(v) for k, v in row.items()}
                for row in cur.fetchall()
            ]

            cur.execute(indexes_sql, (table_name,))
            indexes = [
                {k: _serialize_value(v) for k, v in row.items()}
                for row in cur.fetchall()
            ]

            return {
                "table_name": table_name,
                "columns": columns,
                "indexes": indexes,
            }
    finally:
        conn.close()


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    action = event.get("action", "query")
    database_url = _get_database_url()

    try:
        if action == "list_tables":
            result = _list_tables(database_url)
            return {"ok": True, **result}

        if action == "describe_table":
            table_name = event.get("table_name", "")
            if not table_name:
                return {"ok": False, "error": "table_name is required."}
            result = _describe_table(database_url, table_name)
            if "error" in result:
                return {"ok": False, "error": result["error"]}
            return {"ok": True, **result}

        if action == "query":
            sql = event.get("sql", "")
            max_rows = min(int(event.get("max_rows", 100)), MAX_ROWS)

            validation_error = _validate_sql(sql)
            if validation_error:
                return {"ok": False, "error": validation_error}

            result = _execute_query(database_url, sql, max_rows)
            return {"ok": True, **result}

        return {"ok": False, "error": f"Unknown action: {action}"}

    except psycopg2.Error as exc:
        return {"ok": False, "error": f"Database error: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
