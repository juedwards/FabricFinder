"""Database access for the bot: schema introspection + safe read-only queries."""
import re
from connect import connect, DEFAULT_DATABASE

# Statements that must never appear in a generated query.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|"
    r"REVOKE|EXEC|EXECUTE|SP_|XP_|BACKUP|RESTORE|INTO)\b",
    re.IGNORECASE,
)

MAX_ROWS = 500  # rows returned to the model per query


class QueryError(Exception):
    pass


def _is_read_only(sql: str) -> bool:
    """Allow a single SELECT/WITH statement only."""
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:  # block multiple statements
        return False
    if not re.match(r"^\s*(SELECT|WITH)\b", stripped, re.IGNORECASE):
        return False
    if _FORBIDDEN.search(stripped):
        return False
    return True


def run_query(sql: str, max_rows: int = MAX_ROWS) -> dict:
    """Run a read-only query. Returns {columns, rows, row_count, truncated}."""
    if not _is_read_only(sql):
        raise QueryError(
            "Rejected: only a single read-only SELECT/WITH query is allowed."
        )
    with connect(DEFAULT_DATABASE) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        data = [
            [None if v is None else str(v) for v in row] for row in rows
        ]
    return {
        "columns": columns,
        "rows": data,
        "row_count": len(data),
        "truncated": truncated,
    }


def get_schema_context() -> str:
    """Build a compact text description of all tables/views and columns."""
    with connect(DEFAULT_DATABASE) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'dbo'
            ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION;
            """
        )
        rows = cur.fetchall()
    lines, current = [], None
    for schema, table, col, dtype in rows:
        key = f"{schema}.{table}"
        if key != current:
            lines.append(f"\n{key}")
            current = key
        lines.append(f"  - {col} ({dtype})")
    return "\n".join(lines).strip()


if __name__ == "__main__":
    print(get_schema_context())
